package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Map;
import java.util.SplittableRandom;
import java.util.TreeMap;

import org.apache.hadoop.yarn.api.records.Resource;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningException;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningQuotaException;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class SamplingBiasPolicyTest {
  private static final long BASE = 1_791_808_200_000L;
  private static final long SECOND = 1000L;

  @Test
  void samplingAloneCanAcceptAnOverQuotaReservation() throws PlanningException {
    verifyFalseAcceptance(SECOND);
  }

  @Test
  void falseAcceptanceAlsoOccursWithADayLongWindow() throws PlanningException {
    verifyFalseAcceptance(6 * 3600 * SECOND);
  }

  private static void verifyFalseAcceptance(long tick) throws PlanningException {
    long window = 4 * tick;
    var fixture = PolicyProbeTest.fixture(window, 12, SECOND);
    var plan = fixture.plan();
    var background = PolicyProbeTest.flat(BASE + 2 * tick, BASE + 4 * tick, 20);
    var candidate = PolicyProbeTest.flat(BASE + 4 * tick, BASE + 8 * tick, 10);
    assertTrue(plan.addReservation(background, false));
    var peak = ExactRleWindow.proposed(plan, candidate, window, BASE, BASE + 12 * tick);
    assertEquals(new ExactRleWindow.Peak(60 * tick, BASE + 6 * tick), peak);
    assertTrue(peak.memoryTime() > 12 * window);
    assertThrows(PlanningQuotaException.class, () -> exact(plan, candidate, window, 12));
    assertTrue(plan.addReservation(candidate, false));
    var actual = plan.getConsumptionForUserOverTime(candidate.getUser(), BASE, BASE + 12 * tick);
    assertEquals(20, actual.getCapacityAtTime(BASE + 2 * tick).getMemorySize());
    assertEquals(10, actual.getCapacityAtTime(BASE + 4 * tick).getMemorySize());
  }

  @Test
  void samplingAloneCanRejectAnInQuotaReservation() throws PlanningException {
    long window = 4 * SECOND;
    var fixture = PolicyProbeTest.fixture(window, 25, SECOND);
    var plan = fixture.plan();
    var background = PolicyProbeTest.flat(time(0), time(4), 20);
    var candidate = PolicyProbeTest.flat(time(4), time(7), 20);
    assertTrue(plan.addReservation(background, false));
    assertEquals(new ExactRleWindow.Peak(80 * SECOND, time(4)),
        ExactRleWindow.proposed(plan, candidate, window, time(0), time(11)));
    exact(plan, candidate, window, 25);
    assertThrows(PlanningQuotaException.class, () -> plan.addReservation(candidate, false));
  }

  @Test
  void increasingARequestCanChangeRejectionIntoAcceptance() throws PlanningException {
    long window = 4 * SECOND;
    var fixture = PolicyProbeTest.fixture(window, 25, SECOND);
    var plan = fixture.plan();
    var smaller = PolicyProbeTest.flat(time(0), time(7), 20);
    var larger = PolicyProbeTest.allocation(time(0), time(7), Map.of(
        new ReservationInterval(time(0), time(3)), Resource.newInstance(20, 1),
        new ReservationInterval(time(3), time(4)), Resource.newInstance(24, 1),
        new ReservationInterval(time(4), time(7)), Resource.newInstance(20, 1)));
    for (int t = 0; t < 7; t++) {
      assertTrue(larger.getResourcesAtTime(time(t)).getMemorySize()
          >= smaller.getResourcesAtTime(time(t)).getMemorySize());
    }
    assertEquals(80 * SECOND, ExactRleWindow.proposed(plan, smaller, window,
        time(0), time(11)).memoryTime());
    assertEquals(84 * SECOND, ExactRleWindow.proposed(plan, larger, window,
        time(0), time(11)).memoryTime());
    exact(plan, smaller, window, 25);
    exact(plan, larger, window, 25);
    assertThrows(PlanningQuotaException.class, () -> plan.addReservation(smaller, false));
    assertTrue(plan.addReservation(larger, false));
  }

  private static long time(long relativeSeconds) {
    return BASE + relativeSeconds * SECOND;
  }

  private static final NoOverCommitPolicy INSTANT = new NoOverCommitPolicy();

  /** Test adapter preserving the plan's original instantaneous availability rules. */
  private static void exact(Plan plan, ReservationAllocation candidate, long window,
      long quotaAverage) throws PlanningException {
    INSTANT.validate(plan, candidate);
    var old = plan.getReservationById(candidate.getReservationId());
    long first = Math.subtractExact(candidate.getStartTime(), window);
    long last = Math.addExact(candidate.getEndTime(), window);
    long quota = Math.multiplyExact(quotaAverage, window);
    if (ExactRleWindow.prepare(plan, old, candidate, window, first, last)
        .evaluate(quota).memoryTime() > quota) {
      throw new PlanningQuotaException("Exact memory-time quota exceeded");
    }
  }

  private record Scenario(String label, int segments, int duration, int span) {}

  @Test
  void timeOriginalAndExactValidationOnMatchedDecisions() throws IOException, PlanningException {
    String output = System.getProperty("quota.samplingTimingFile");
    if (output == null) return;
    var rows = new ArrayList<String>();
    rows.add("scenario,background_segments,background_seconds,request_seconds,accepted,"
        + "repeat,original_ns,exact_ns");
    long window = 32 * SECOND;
    long quota = 25;
    var random = new SplittableRandom(82126);
    for (var s : new Scenario[]{
        new Scenario("dense128_short", 128, 128, 8),
        new Scenario("dense128_wide", 128, 128, 64),
        new Scenario("dense1024_short", 1024, 1024, 8),
        new Scenario("dense1024_wide", 1024, 1024, 512),
        new Scenario("one_run_many_windows", 1, 8192, 4096)}) {
      var fixture = PolicyProbeTest.fixture(window, (int) quota, SECOND);
      var profile = new TreeMap<ReservationInterval, Resource>();
      int step = s.duration / s.segments;
      for (int i = 0; i < s.segments; i++) {
        profile.put(new ReservationInterval(time((long) i * step), time((i + 1L) * step)),
            Resource.newInstance(1 + i % 4, 1));
      }
      assertTrue(fixture.plan().addReservation(
          PolicyProbeTest.allocation(time(0), time(s.duration), profile), true));
      // Requests end well before the profile's end. The pinned policy is used
      // for timing to avoid the known trunk overflow on this truncated view.
      long start = time(16), end = time(16 + s.span);
      var accepted = PolicyProbeTest.flat(start, end, 8);
      // Leave room for background usage in the real instantaneous policy.
      var rejected = PolicyProbeTest.flat(start, end, 96);
      var durations = new long[2];
      var actual = new boolean[2];
      for (int rep = -80; rep < 160; rep++) {
        boolean firstAccept = random.nextBoolean();
        for (int outcome = 0; outcome < 2; outcome++) {
          boolean expected = (outcome == 0) == firstAccept;
          var candidate = expected ? accepted : rejected;
          int first = random.nextInt(2);
          for (int pass = 0; pass < 2; pass++) {
            int mode = pass ^ first;
            boolean allow = true;
            long started = System.nanoTime();
            try {
              if (mode == 0) fixture.policy().validate(fixture.plan(), candidate);
              else exact(fixture.plan(), candidate, window, quota);
            } catch (PlanningQuotaException denied) {
              allow = false;
            }
            long finished = System.nanoTime();
            durations[mode] = finished - started;
            actual[mode] = allow;
          }
          assertEquals(expected, actual[0], s.label + " original");
          assertEquals(expected, actual[1], s.label + " exact");
          if (rep >= 0) rows.add(s.label + "," + s.segments + "," + s.duration + ","
              + s.span + "," + expected + "," + rep + "," + durations[0] + "," + durations[1]);
        }
      }
    }
    Files.write(Path.of(output), rows);
  }
}
