package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.TreeMap;
import java.util.TreeSet;

import org.apache.hadoop.yarn.api.records.ReservationId;
import org.apache.hadoop.yarn.api.records.Resource;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningException;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningQuotaException;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.RLESparseResourceAllocation.RLEOperator;
import org.apache.hadoop.yarn.util.resource.DefaultResourceCalculator;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ExactWindowTest {
  private static final long BASE = 1_791_808_200_000L;
  private static final long SECOND = 1_000L;
  private static final long MINUTE = 60 * SECOND;
  private static final long HOUR = 3_600 * SECOND;
  private static final long DAY_START = BASE - BASE % (24 * HOUR);
  private static final Resource MIN = Resource.newInstance(1, 1);

  record Shape(int start, int end, int demand, int period) {
    int at(int t) {
      if (period == 0) return start <= t && t < end ? demand : 0;
      return Math.floorMod(t - start, period) < end - start ? demand : 0;
    }
  }

  record Peak(long usage, int time) {}

  static Peak dense(List<Shape> shapes, int window, int horizon) {
    return dense(shapes, window, 0, horizon);
  }

  private static Peak dense(List<Shape> shapes, int window, int first, int last) {
    // Evaluate every integer slot without using slope jumps or the YARN RLE.
    int start = first - window;
    long[] load = new long[last - start];
    for (int t = start; t < last; t++) {
      for (Shape s : shapes) load[t - start] += s.at(t);
    }
    long sum = 0;
    for (int i = 0; i < window; i++) sum += load[i];
    Peak best = new Peak(sum, first);
    for (int t = first + 1; t <= last; t++) {
      sum += load[t - start - 1] - load[t - first - 1];
      if (sum > best.usage()) best = new Peak(sum, t);
    }
    return best;
  }

  static Peak sweep(List<Shape> shapes, int window, int horizon) {
    TreeMap<Integer, Long> jumps = new TreeMap<>();
    for (Shape s : shapes) {
      if (s.period() == 0) {
        addClipped(jumps, s.start(), s.end(), s.demand(), window, horizon);
      } else {
        int shift = (Math.floorDiv(-window - s.end(), s.period()) + 1) * s.period();
        for (; s.start() + shift < horizon; shift += s.period()) {
          addClipped(jumps, s.start() + shift, s.end() + shift, s.demand(), window, horizon);
        }
      }
    }
    jumps.putIfAbsent(0, 0L);
    jumps.putIfAbsent(horizon, 0L);
    long value = 0, slope = 0;
    int previous = -window;
    Peak best = null;
    for (var event : jumps.entrySet()) {
      int t = event.getKey();
      if (t > horizon) break;
      value += slope * (t - previous);
      if (t >= 0 && (best == null || value > best.usage())) best = new Peak(value, t);
      slope += event.getValue();
      previous = t;
    }
    return best;
  }

  private static void addClipped(TreeMap<Integer, Long> jumps, int a, int b,
      long d, int window, int horizon) {
    a = Math.max(a, -window);
    b = Math.min(b, horizon);
    if (a >= b) return;
    jumps.merge(a, d, Long::sum);
    jumps.merge(b, -d, Long::sum);
    jumps.merge(a + window, -d, Long::sum);
    jumps.merge(b + window, d, Long::sum);
  }

  private static InMemoryReservationAllocation materialize(Shape s, ReservationId id, long tick) {
    return materialize(s, id, tick, s.period() == 0 ? BASE : 0, "oracle");
  }

  private static InMemoryReservationAllocation materialize(Shape s, ReservationId id,
      long tick, long origin, String user) {
    long start = origin + s.start() * tick, end = origin + s.end() * tick;
    long left = start, right = end;
    long p = s.period() * tick;
    Map<ReservationInterval, Resource> intervals = new TreeMap<>();
    Resource demand = Resource.newInstance(s.demand(), 1);
    if (p == 0) {
      intervals.put(new ReservationInterval(left, right), demand);
    } else {
      left = Math.floorMod(start, p);
      right = left + (end - start);
      if (right > p) {
        intervals.put(new ReservationInterval(left, p), demand);
        intervals.put(new ReservationInterval(0, right - p), demand);
        left = 0;
        right = p;
      } else intervals.put(new ReservationInterval(left, right), demand);
    }
    var definition = ReservationSystemTestUtil.createSimpleReservationDefinition(
        start, end, end - start, 1, p == 0 ? null : Long.toString(p));
    return new InMemoryReservationAllocation(id, definition, user,
        ReservationSystemTestUtil.getFullReservationQueueName(), left, right, intervals,
        new DefaultResourceCalculator(), MIN);
  }

  private static void probe(List<String> output, String group, int index,
      PolicyProbeTest.Fixture fixture, List<Shape> kept, Shape candidate,
      ReservationId id, int window, int quota, long tick, int horizon)
      throws PlanningException {
    List<Shape> proposed = new ArrayList<>(kept);
    proposed.add(candidate);
    Peak truth = dense(proposed, window, horizon);
    assertEquals(truth, sweep(proposed, window, horizon), group + ":" + index);
    assertTrue(proposed.stream().mapToInt(Shape::demand).sum() <= 100);
    boolean exact = truth.usage() <= (long) quota * window;
    var request = materialize(candidate, id, tick);
    long origin = candidate.period() == 0 ? BASE : DAY_START;
    var peak = ExactRleWindow.proposed(fixture.plan(), request,
        window * tick, origin, origin + horizon * tick);
    assertEquals(new ExactRleWindow.Peak(truth.usage() * tick, origin + truth.time() * tick),
        peak, group + ":" + index);
    assertEquals(peak, YarnBridgeWindow.proposed(fixture.plan(), request,
        window * tick, origin, origin + horizon * tick), group + ":" + index);
    boolean rleAccept = peak.memoryTime() <= quota * window * tick;
    assertEquals(exact, rleAccept, group + ":" + index);
    boolean yarn = true;
    String reason = "accepted";
    try {
      fixture.policy().validate(fixture.plan(), request);
    } catch (PlanningQuotaException rejected) {
      yarn = false;
      reason = rejected.getMessage().startsWith("Integral") ? "integral" : "other";
    }
    output.add(group + "," + index + "," + kept.size() + "," + candidate.start()
        + "," + candidate.end() + "," + candidate.demand() + "," + candidate.period()
        + "," + window + "," + quota + "," + truth.usage() + "," + truth.time()
        + "," + exact + "," + yarn + "," + reason + "," + peak.memoryTime()
        + "," + peak.windowEnd() + "," + rleAccept);
  }

  private static void install(PolicyProbeTest.Fixture fixture, List<Shape> shapes, long tick)
      throws PlanningException {
    for (Shape s : shapes) {
      assertTrue(fixture.plan().addReservation(
          materialize(s, ReservationSystemTestUtil.getNewReservationId(), tick), true));
    }
  }

  private static ExactRleWindow.Peak checkRle(PolicyProbeTest.Fixture fixture,
      InMemoryReservationAllocation candidate, List<Shape> shapes, long origin,
      long tick, int window, int first, int last, String label) throws PlanningException {
    Peak expected = dense(shapes, window, first, last);
    var actual = ExactRleWindow.proposed(fixture.plan(), candidate,
        window * tick, origin + first * tick, origin + last * tick);
    assertEquals(new ExactRleWindow.Peak(expected.usage() * tick,
        origin + expected.time() * tick), actual, label);
    assertEquals(actual, YarnBridgeWindow.proposed(fixture.plan(), candidate, window * tick,
        origin + first * tick, origin + last * tick), label);
    return actual;
  }

  @Test
  void mixedAndReplacingProfilesWithShortWindows() throws PlanningException, IOException {
    var rows = new ArrayList<String>();
    rows.add("case,window_min,first_min,last_min,old_period_min,new_period_min,"
        + "peak_mib_ms,earliest_window_end_ms,exact_accept");
    var rng = new Random(661761);
    long origin = DAY_START + 17 * MINUTE;
    int day = 24 * 60;
    for (int i = 0; i < 128; i++) {
      int window = 4 + rng.nextInt(81);
      int first = day - 35 + rng.nextInt(72);
      int last = i % 16 == 0 ? first : first + 1 + rng.nextInt(91);
      var fixture = PolicyProbeTest.fixture(window * MINUTE, 25, MINUTE);
      var recurring = new Shape(day - 27, day + 7, 2 + rng.nextInt(12), day);
      var once = new Shape(day - 43, day + 20, 2 + rng.nextInt(12), 0);
      var old = new Shape(day - 33, day + 25, 5 + rng.nextInt(12),
          (i & 1) == 0 ? day : 0);
      var unrelated = new Shape(day - 120, day + 150, 50, 0);
      for (var item : List.of(recurring, once, unrelated)) {
        assertTrue(fixture.plan().addReservation(materialize(item,
            ReservationSystemTestUtil.getNewReservationId(), MINUTE, origin,
            item == unrelated ? "other-user" : "oracle"), true));
      }
      var id = ReservationSystemTestUtil.getNewReservationId();
      var installed = materialize(old, id, MINUTE, origin, "oracle");
      assertTrue(fixture.plan().addReservation(installed, true));
      int begin = day - 40 + rng.nextInt(55);
      var replacement = new Shape(begin, begin + 1 + rng.nextInt(85), 8 + rng.nextInt(56),
          (i & 2) == 0 ? day : 0);
      var request = materialize(replacement, id, MINUTE, origin, "oracle");
      var shapes = List.of(recurring, once, replacement);
      var actual = checkRle(fixture, request, shapes, origin, MINUTE, window,
          first, last, "mixed:" + i);
      boolean accept = actual.memoryTime() <= 25 * window * MINUTE;
      assertEquals(dense(shapes, window, first, last).usage() <= 25L * window, accept);
      assertEquals(installed, fixture.plan().getReservationById(id));
      rows.add(i + "," + window + "," + first + "," + last + "," + old.period()
          + "," + replacement.period() + "," + actual.memoryTime() + ","
          + actual.windowEnd() + "," + accept);
    }
    String path = System.getProperty("quota.mixedFile");
    if (path != null) Files.write(Path.of(path), rows);
  }

  @Test
  void exactReplayOfThreeUpstreamRegressions() throws PlanningException {
    int window = 24;
    long w = window * HOUR;
    var cutoff = PolicyProbeTest.fixture(w, 25, HOUR);
    var background = new Shape(30, 32, 10, 0);
    var shortRequest = new Shape(6, 7, 10, 0);
    install(cutoff, List.of(background), HOUR);
    var request = materialize(shortRequest, ReservationSystemTestUtil.getNewReservationId(), HOUR);
    assertEquals(new ExactRleWindow.Peak(10 * HOUR, BASE + 7 * HOUR),
        checkRle(cutoff, request, List.of(background, shortRequest), BASE, HOUR, window,
            -18, 31, "cutoff"));

    int day = 24 * 60;
    var periodic = PolicyProbeTest.fixture(w, 25, MINUTE);
    var wrap = new Shape(day - 30, day + 30, 5, day);
    var daily = new ArrayList<Shape>();
    for (int i = 1; i <= 3; i++) {
      var next = materialize(wrap, ReservationSystemTestUtil.getNewReservationId(), MINUTE);
      daily.add(wrap);
      assertEquals(new ExactRleWindow.Peak(i * 5 * HOUR, DAY_START - w),
          checkRle(periodic, next, daily, DAY_START, MINUTE, day, -day, 2 * day, "wrap:" + i));
      assertTrue(periodic.plan().addReservation(next, false));
    }

    var update = PolicyProbeTest.fixture(w, 25, HOUR);
    var id = ReservationSystemTestUtil.getNewReservationId();
    var old = materialize(new Shape(0, 20, 20, 0), id, HOUR);
    assertTrue(update.plan().addReservation(old, false));
    for (int demand : new int[]{20, 30}) {
      var shape = new Shape(0, 21, demand, 0);
      var next = materialize(shape, id, HOUR);
      var result = checkRle(update, next, List.of(shape), BASE, HOUR, window,
          -window, 21 + window, "update:" + demand);
      assertEquals(new ExactRleWindow.Peak(21 * demand * HOUR, BASE + 21 * HOUR), result);
      assertEquals(demand == 20, result.memoryTime() <= 25 * w);
      if (demand == 20) {
        assertTrue(update.plan().updateReservation(next));
      } else {
        assertThrows(PlanningQuotaException.class, () -> update.plan().updateReservation(next));
      }
    }
  }

  @Test
  void traceCutoffCaseBeforeRounding() throws PlanningException, IOException {
    int window = 8;
    long w = window * SECOND;
    var fixture = PolicyProbeTest.fixture(w, 25, SECOND);
    var history = List.of(new Shape(0, 6, 9, 0), new Shape(18, 24, 5, 0));
    install(fixture, history, SECOND);
    Shape request = new Shape(10, 12, 79, 0);
    var candidate = materialize(request, ReservationSystemTestUtil.getNewReservationId(), SECOND);
    assertThrows(PlanningQuotaException.class,
        () -> fixture.policy().validate(fixture.plan(), candidate));
    var proposed = new ArrayList<>(history);
    proposed.add(request);
    assertEquals(new Peak(176, 12), dense(proposed, window, 32));
    assertEquals(new ExactRleWindow.Peak(176 * SECOND, BASE + 12 * SECOND),
        ExactRleWindow.proposed(fixture.plan(), candidate, w, BASE, BASE + 32 * SECOND));
    long checkStart = candidate.getStartTime() - w, checkEnd = candidate.getEndTime() + w;
    var plan = fixture.plan();
    var merged = RLESparseResourceAllocation.merge(plan.getResourceCalculator(), plan.getTotalCapacity(),
        plan.getConsumptionForUserOverTime(candidate.getUser(), checkStart, checkEnd),
        candidate.getResourcesOverTime(checkStart, checkEnd), RLEOperator.add,
        Long.MIN_VALUE, checkEnd + w).getCumulative();
    // Reproduce the timestamps at which the pinned policy stores cumulative usage.
    TreeMap<Long, Resource> checkpoints = new TreeMap<>(merged);
    for (var current : merged.entrySet()) {
      var next = merged.higherEntry(current.getKey());
      if (next == null || next.getValue() == null) continue;
      for (long i = 1; i <= (next.getKey() - current.getKey()) / w; i++) {
        checkpoints.putIfAbsent(current.getKey() + i*w, current.getValue());
      }
    }
    TreeMap<Long, Long> prefix = new TreeMap<>();
    long integral = 0, previous = checkpoints.firstKey(), rate = 0;
    for (var current : checkpoints.entrySet()) {
      integral += rate * (current.getKey() - previous);
      prefix.put(current.getKey(), integral);
      previous = current.getKey();
      rate = current.getValue() == null ? 0 : current.getValue().getMemorySize();
    }
    TreeSet<Long> check = new TreeSet<>(prefix.keySet());
    for (long x : prefix.keySet()) check.add(x + w);
    check.add(checkStart);
    check.add(checkEnd);
    long largest = 0;
    var output = new ArrayList<String>();
    output.add("window_end_s,exact_mib_s,sampled_mib_s,rounded_average,upper_sample_s,lower_sample_s");
    for (long t : check.subSet(checkStart, true, checkEnd, true)) {
      var up = prefix.floorEntry(t);
      var down = prefix.floorEntry(t - w);
      long top = up == null ? 0 : up.getValue(), bottom = down == null ? 0 : down.getValue();
      long sampled = Math.round((double) top / w) - Math.round((double) bottom / w);
      largest = Math.max(largest, sampled);
      int end = (int) ((t - BASE) / SECOND);
      long exact = 0;
      for (int s = end - window; s < end; s++) for (Shape shape : proposed) exact += shape.at(s);
      if (end == 12) {
        assertEquals(176L, exact);
        assertEquals(BASE, down.getKey());
        assertEquals(212 * SECOND, top - bottom);
      }
      output.add(end + "," + exact + "," + (top - bottom) / SECOND + "," + sampled + ","
          + (up == null ? "" : (up.getKey() - BASE) / SECOND) + ","
          + (down == null ? "" : (down.getKey() - BASE) / SECOND));
    }
    assertTrue(largest > 25, "expected sampling to exceed the 25 MiB quota");
    String path = System.getProperty("quota.traceFile");
    if (path != null) Files.write(Path.of(path), output);
  }

  @Test
  void compareExactWindowsWithPinnedPolicy() throws PlanningException, IOException {
    List<String> output = new ArrayList<>();
    output.add("group,case,kept_count,start,end,demand,period,window,quota,"
        + "exact_peak,earliest_window_end,exact_accept,yarn_accept,yarn_reason,"
        + "rle_peak_mib_ms,rle_earliest_window_end_ms,rle_accept");
    var empty = PolicyProbeTest.fixture(4 * SECOND, 10, SECOND);
    for (int length = 1; length <= 4; length++) {
      for (int demand = 1; demand <= 100; demand++) {
        probe(output, "single", 100 * (length - 1) + demand, empty, List.of(),
            new Shape(3, 3 + length, demand, 0), ReservationSystemTestUtil.getNewReservationId(),
            4, 10, SECOND, 12);
      }
    }
    Random rng = new Random(261119);
    for (int i = 0; i < 64; i++) {
      var cutoff = PolicyProbeTest.fixture(8 * SECOND, 25, SECOND);
      var history = List.of(new Shape(0, 6, 2 + rng.nextInt(8), 0),
          new Shape(18, 24, 2 + rng.nextInt(8), 0));
      install(cutoff, history, SECOND);
      probe(output, "cutoff", i, cutoff, history, new Shape(10, 12, 10 + rng.nextInt(71), 0),
          ReservationSystemTestUtil.getNewReservationId(), 8, 25, SECOND, 32);

      var replace = PolicyProbeTest.fixture(8 * SECOND, 25, SECOND);
      var other = List.of(new Shape(3, 19, 2 + rng.nextInt(6), 0),
          new Shape(9, 15, 2 + rng.nextInt(6), 0));
      install(replace, other, SECOND);
      var oldId = ReservationSystemTestUtil.getNewReservationId();
      assertTrue(replace.plan().addReservation(
          materialize(new Shape(7, 13, 5, 0), oldId, SECOND), true));
      int start = 6 + rng.nextInt(6);
      probe(output, "replace", i, replace, other,
          new Shape(start, start + 2 + rng.nextInt(13), 8 + rng.nextInt(56), 0),
          oldId, 8, 25, SECOND, 36);

      var periodic = PolicyProbeTest.fixture(24 * HOUR, 25, HOUR);
      var recurring = List.of(new Shape(22, 26, 6 + rng.nextInt(15), 24));
      install(periodic, recurring, HOUR);
      int pStart = 21 + rng.nextInt(3);
      probe(output, "periodic_wrap", i, periodic, recurring,
          new Shape(pStart, 25 + rng.nextInt(9), 12 + rng.nextInt(63), 24),
          ReservationSystemTestUtil.getNewReservationId(), 24, 25, HOUR, 24);
    }
    assertEquals(593, output.size());
    String path = System.getProperty("quota.exactFile");
    if (path != null) Files.write(Path.of(path), output);
  }
}
