package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.SplittableRandom;
import java.util.TreeMap;

import org.apache.hadoop.yarn.api.records.ReservationId;
import org.apache.hadoop.yarn.api.records.Resource;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningException;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningQuotaException;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.planning.ReservationAgent;
import org.apache.hadoop.yarn.server.resourcemanager.scheduler.QueueMetrics;
import org.apache.hadoop.yarn.util.resource.DefaultResourceCalculator;
import org.junit.jupiter.api.Test;

import static org.apache.hadoop.yarn.server.resourcemanager.reservation.QuotaChanges.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.mock;

/** Runs the actual InMemoryPlan write methods with three exact quota policies. */
class YarnPlanReplayTest {
  private static final long ORIGIN = 1_791_808_200_000L;
  private static final long STEP = 1000L;
  private static final long WINDOW = 32 * STEP;
  private static final String USER = "replay";
  private static final String QUEUE = ReservationSystemTestUtil.getFullReservationQueueName();
  private static final Resource MIN = Resource.newInstance(1, 1);
  private static final Resource TOTAL = Resource.newInstance(1_000_000, 1_000_000);

  enum Mode { RLE, EVENT, BRIDGE }

  private record Operation(ReservationId id, InMemoryReservationAllocation next,
      boolean replace, boolean expected, int segments) {
    String kind() {
      return next == null ? "delete" : replace ? "replace" : "add";
    }
  }

  private static final class WindowRejected extends PlanningQuotaException {
    WindowRejected() {
      super("Exact sliding window quota exceeded");
    }
  }

  private static final class Stats {
    long total, read, check, instant, plan, index;
    int callbacks;
    boolean accepted;
  }

  private static final class ExactPolicy extends NoOverCommitPolicy {
    private final CapacityOverTimePolicy instantPolicy = new CapacityOverTimePolicy();
    private final Mode mode;
    private final long quota, end;
    private final EventQuotaIndex events;
    private final BridgeQuotaIndex tree;
    private Stats active;
    private Transaction pending;
    private ReservationAllocation old;
    private long policyTime;

    ExactPolicy(Mode mode, long quota, long end, TreeMap<Long, Long> background) {
      this.mode = mode;
      this.quota = quota;
      this.end = end;
      events = mode == Mode.EVENT ? new EventQuotaIndex(background) : null;
      tree = mode == Mode.BRIDGE ? new BridgeQuotaIndex(background, end) : null;
      init(QUEUE, ReservationSystemTestUtil.createConf(QUEUE, WINDOW, 100, 100));
    }

    @Override
    public void init(String queue, ReservationSchedulerConfiguration conf) {
      instantPolicy.init(queue, conf);
    }

    @Override
    public long getValidWindow() {
      return WINDOW;
    }

    @Override
    public RLESparseResourceAllocation availableResources(RLESparseResourceAllocation available,
        Plan plan, String user, ReservationId id, long start, long end) throws PlanningException {
      return instantPolicy.availableResources(available, plan, user, id, start, end);
    }

    @Override
    public void validate(Plan plan, ReservationAllocation candidate) throws PlanningException {
      long entry = System.nanoTime();
      try {
        super.validate(plan, candidate);
        long afterInstant = System.nanoTime();
        active.instant += afterInstant - entry;
        // updateReservation validates again after removing the old allocation.
        // The final proposed profile is identical and the outer operation owns both calls.
        if (active.callbacks++ != 0) return;
        old = plan.getReservationById(candidate.getReservationId());
        boolean allowed;
        long prepared;
        if (mode == Mode.RLE) {
          // Removing the old reservation can only decrease usage outside this range.
          var input = ExactRleWindow.prepare(plan, old, candidate, WINDOW,
              candidate.getStartTime(), Math.addExact(candidate.getEndTime(), WINDOW));
          prepared = System.nanoTime();
          allowed = input.evaluate(quota).memoryTime() <= quota;
        } else {
          pending = betweenJumps(jumps(old), jumps(candidate), end);
          prepared = System.nanoTime();
          allowed = events != null ? events.allows(pending, quota) : tree.allows(pending, quota);
        }
        long checked = System.nanoTime();
        active.read += prepared - afterInstant;
        active.check += checked - prepared;
        if (!allowed) throw new WindowRejected();
      } finally {
        policyTime += System.nanoTime() - entry;
      }
    }

    // Serial write driver. It is the sole writer to this plan in the experiment.
    synchronized Stats execute(InMemoryPlan plan, Operation operation) throws PlanningException {
      Stats stats = new Stats();
      active = stats;
      pending = null;
      old = null;
      policyTime = 0;
      long started = System.nanoTime();
      try {
        if (operation.next == null && mode != Mode.RLE) {
          old = plan.getReservationById(operation.id);
          pending = betweenJumps(jumps(old), new TreeMap<>(), end);
          stats.read = System.nanoTime() - started;
        }
        long writeStart = System.nanoTime();
        try {
          if (operation.next == null) stats.accepted = plan.deleteReservation(operation.id);
          else if (operation.replace) stats.accepted = plan.updateReservation(operation.next);
          else stats.accepted = plan.addReservation(operation.next, false);
        } catch (WindowRejected expected) {
          stats.accepted = false;
        }
        long planDone = System.nanoTime();
        stats.plan = planDone - writeStart - policyTime;
        if (stats.accepted && pending != null) {
          if (events != null) events.commit(pending);
          else tree.commit(pending);
        }
        long finished = System.nanoTime();
        stats.index = stats.accepted && pending != null ? finished - planDone : 0;
        stats.total = finished - started;
        return stats;
      } finally {
        active = null;
        pending = null;
        old = null;
      }
    }

    long valueAt(long relativeTime) {
      return events != null ? events.valueAt(relativeTime) : tree.valueAt(relativeTime);
    }
  }

  private record Driver(ExactPolicy policy, InMemoryPlan plan) {}

  private static Driver driver(Mode mode, long quota, long end,
      InMemoryReservationAllocation background, QueueMetrics metrics, ReservationAgent agent)
      throws PlanningException {
    var policy = new ExactPolicy(mode, quota, end,
        mode == Mode.RLE ? new TreeMap<>() : jumps(background));
    var plan = new InMemoryPlan(metrics, policy, agent, TOTAL, STEP,
        new DefaultResourceCalculator(), MIN, TOTAL, QUEUE, null, true,
        ReservationSystemTestUtil.createMockRMContext());
    assertTrue(plan.addReservation(background, true));
    return new Driver(policy, plan);
  }

  private static TreeMap<Long, Long> jumps(ReservationAllocation allocation) {
    var result = new TreeMap<Long, Long>();
    if (allocation == null) return result;
    if (allocation.getPeriodicity() != 0 || !USER.equals(allocation.getUser())) {
      throw new IllegalArgumentException("Replay indexes one user with nonperiodic allocations");
    }
    allocation.getAllocationRequests().forEach((interval, resource) -> addReservation(result,
        new Reservation(interval.getStartTime() - ORIGIN, interval.getEndTime() - ORIGIN,
            resource.getMemorySize()), WINDOW));
    return result;
  }

  private static InMemoryReservationAllocation allocation(ReservationId id, long start, long end,
      Map<ReservationInterval, Resource> intervals) {
    var definition = ReservationSystemTestUtil.createSimpleReservationDefinition(
        start, end, end - start, 1, null);
    return new InMemoryReservationAllocation(id, definition, USER, QUEUE, start, end, intervals,
        new DefaultResourceCalculator(), MIN);
  }

  private static InMemoryReservationAllocation materialize(ReservationId id, Reservation r) {
    long start = ORIGIN + r.start() * STEP, end = ORIGIN + r.end() * STEP;
    return allocation(id, start, end, Map.of(new ReservationInterval(start, end),
        Resource.newInstance(r.demand(), 1)));
  }

  private static List<Operation> operations(JavaQuotaReplayTest.Stream stream) {
    var ids = new HashMap<Reservation, ArrayDeque<ReservationId>>();
    var result = new ArrayList<Operation>();
    for (var q : stream.requests()) {
      boolean replace = q.old().demand() != 0;
      ReservationId id = replace ? ids.get(q.old()).element()
          : ReservationSystemTestUtil.getNewReservationId();
      var next = q.next().demand() == 0 ? null : materialize(id, q.next());
      result.add(new Operation(id, next, replace, q.accepted(), next == null ? 0 : 1));
      if (q.accepted()) {
        if (replace) ids.get(q.old()).remove();
        if (next != null) ids.computeIfAbsent(q.next(), k -> new ArrayDeque<>()).add(id);
      }
    }
    return result;
  }

  @Test
  void writePath() throws PlanningException, IOException {
    var input = Path.of("../results/native-replay/input.txt");
    String output = System.getProperty("quota.planSamples");
    int repeats = Integer.getInteger("quota.planRepeats", output == null ? 1 : 3);
    int warmups = Integer.getInteger("quota.planWarmups", output == null ? 0 : 1);
    int scale = Integer.getInteger("quota.planScale", output == null ? 2048 : 0);
    var rows = new ArrayList<String>();
    rows.add("seed,background_slots,span,target_rejection_pct,repeat,request,kind,segments,"
        + "mode,accepted,callbacks,total_ns,extract_ns,exact_check_ns,instant_ns,plan_ns,index_ns");
    var metrics = mock(QueueMetrics.class);
    var agent = mock(ReservationAgent.class);
    int checked = 0;
    for (var w : JavaQuotaReplayTest.read(input)) {
      if (scale != 0 && w.slots() != scale) continue;
      var profile = new TreeMap<ReservationInterval, Resource>();
      for (int i = 0; i < w.slots(); i++) {
        long start = ORIGIN + 4L * i * STEP;
        profile.put(new ReservationInterval(start, start + 4 * STEP),
            Resource.newInstance(w.load()[4 * i], 1));
      }
      var background = allocation(ReservationSystemTestUtil.getNewReservationId(),
          ORIGIN, ORIGIN + 4L * w.slots() * STEP, profile);
      var order = new SplittableRandom(w.seed());
      var engines = new EnumMap<Mode, Driver>(Mode.class);
      for (Mode mode : Mode.values()) {
        engines.put(mode, driver(mode, w.quota() * STEP, w.end() * STEP,
            background, metrics, agent));
      }
      var initial = memoryProfile(engines.get(Mode.RLE).plan, w.end() * STEP);
      for (var stream : w.streams()) {
        List<Operation> requests = operations(stream);
        var live = new LinkedHashSet<ReservationId>();
        for (var request : requests) {
          if (!request.expected) continue;
          if (request.next == null) live.remove(request.id);
          else live.add(request.id);
        }
        for (int repeat = -warmups; repeat < repeats; repeat++) {
          Mode[] modes = Mode.values();
          for (int i = 0; i < requests.size(); i++) {
            var request = requests.get(i);
            int first = order.nextInt(modes.length);
            int direction = order.nextBoolean() ? 1 : -1;
            for (int j = 0; j < modes.length; j++) {
              Mode mode = modes[Math.floorMod(first + j * direction, modes.length)];
              Driver driver = engines.get(mode);
              var prior = driver.plan.getReservationById(request.id);
              Stats s = driver.policy.execute(driver.plan, request);
              assertEquals(request.expected, s.accepted, mode + " request " + i);
              assertEquals(request.next == null ? 0 : request.replace && s.accepted ? 2 : 1,
                  s.callbacks, mode + " callback count");
              assertSame(s.accepted ? request.next : prior, driver.plan.getReservationById(request.id));
              if (output != null && repeat >= 0) {
                rows.add(w.seed() + "," + w.slots() + "," + stream.span() + ","
                    + stream.rejection() + "," + repeat + "," + i + "," + request.kind() + ","
                    + request.segments + "," + mode + "," + s.accepted + "," + s.callbacks + ","
                    + s.total + "," + s.read + "," + s.check + "," + s.instant + "," + s.plan + "," + s.index);
              }
            }
            if (repeat == 0) checked++;
          }
          verifyState(engines, w.end() * STEP);
          for (var driver : engines.values()) {
            for (var id : live) {
              assertTrue(driver.policy.execute(driver.plan, new Operation(id, null, false, true, 0)).accepted);
            }
            assertEquals(1, driver.plan.getAllReservations().size());
            assertEquals(initial, memoryProfile(driver.plan, w.end() * STEP), "reset to background");
          }
          verifyState(engines, w.end() * STEP);
        }
        System.out.println("Completed " + w.slots() + "/" + w.seed() + "/"
            + stream.span() + "/" + stream.rejection());
      }
    }
    assertTrue(checked > 0);
    System.out.println("YARN InMemoryPlan write-path requests checked: " + checked);
    if (output != null) Files.write(Path.of(output), rows);
  }

  private static TreeMap<Long, Long> memoryProfile(InMemoryPlan plan, long end) {
    var result = new TreeMap<Long, Long>();
    long rate = 0;
    var rle = plan.getConsumptionForUserOverTime(USER, ORIGIN - WINDOW, ORIGIN + end);
    for (var entry : rle.getCumulative().entrySet()) {
      long next = entry.getValue() == null ? 0 : entry.getValue().getMemorySize();
      if (next != rate) {
        result.put(entry.getKey(), next);
        rate = next;
      }
    }
    return result;
  }

  @Test
  void multiSegmentWrites() throws PlanningException, IOException {
    String output = System.getProperty("quota.planMultiSamples");
    int warmups = output == null ? 0 : 8;
    int repeats = output == null ? 1 : 30;
    var rows = new ArrayList<String>();
    rows.add("segments,repeat,kind,mode,accepted,total_ns,extract_ns,exact_check_ns,"
        + "instant_ns,plan_ns,index_ns");
    var metrics = mock(QueueMetrics.class);
    var agent = mock(ReservationAgent.class);
    int slots = 2048;
    long end = 4L * slots * STEP + WINDOW;
    var rng = new SplittableRandom(9241);
    var profile = new TreeMap<ReservationInterval, Resource>();
    for (int i = 0; i < slots; i++) {
      long start = ORIGIN + 4L * i * STEP;
      profile.put(new ReservationInterval(start, start + 4 * STEP),
          Resource.newInstance(rng.nextInt(2, 14), 1));
    }
    var background = allocation(ReservationSystemTestUtil.getNewReservationId(),
        ORIGIN, ORIGIN + 4L * slots * STEP, profile);
    for (int count : new int[]{1, 4, 16, 64}) {
      var engines = new EnumMap<Mode, Driver>(Mode.class);
      for (Mode mode : Mode.values()) {
        engines.put(mode, driver(mode, 600 * STEP, end, background, metrics, agent));
      }
      var id = ReservationSystemTestUtil.getNewReservationId();
      long start = ORIGIN + 256 * STEP;
      long span = 4096 * STEP;
      var original = segmented(id, start, span, count, 1);
      var replacement = segmented(id, start + 2 * STEP, span, count, 2);
      var invalid = segmented(id, start + STEP, span, count, 1000);
      var requests = List.of(new Operation(id, original, false, true, count),
          new Operation(id, invalid, true, false, count),
          new Operation(id, replacement, true, true, count),
          new Operation(id, null, false, true, count));
      Mode[] modes = Mode.values();
      for (int repeat = -warmups; repeat < repeats; repeat++) {
        for (Operation request : requests) {
          int first = rng.nextInt(modes.length);
          for (int j = 0; j < modes.length; j++) {
            Mode mode = modes[(first + j) % modes.length];
            Driver driver = engines.get(mode);
            var prior = driver.plan.getReservationById(id);
            Stats s = driver.policy.execute(driver.plan, request);
            assertEquals(request.expected, s.accepted, mode + " " + count + " " + request.kind());
            assertSame(s.accepted ? request.next : prior, driver.plan.getReservationById(id));
            if (output != null && repeat >= 0) {
              String kind = request.expected ? request.kind() : "rejected_replace";
              rows.add(count + "," + repeat + "," + kind + "," + mode + "," + s.accepted
                  + "," + s.total + "," + s.read + "," + s.check + "," + s.instant
                  + "," + s.plan + "," + s.index);
            }
          }
        }
      }
      verifyState(engines, end);
    }
    if (output != null) Files.write(Path.of(output), rows);
  }

  private static InMemoryReservationAllocation segmented(ReservationId id, long start,
      long span, int count, long demand) {
    var intervals = new TreeMap<ReservationInterval, Resource>();
    long stride = span / count;
    for (int j = 0; j < count; j++) {
      long left = start + j * stride;
      intervals.put(new ReservationInterval(left, left + stride / 2),
          Resource.newInstance(demand + j % 2, 1));
    }
    return allocation(id, start, start + span, intervals);
  }

  private static void verifyState(EnumMap<Mode, Driver> engines, long end) {
    var truth = engines.get(Mode.RLE).plan;
    var expected = truth.getConsumptionForUserOverTime(USER, ORIGIN - WINDOW, ORIGIN + end)
        .getCumulative();
    for (long t = 0; t <= end; t += Math.max(STEP, end / 53)) {
      long total = 0, absolute = ORIGIN + t, start = absolute - WINDOW;
      var initial = expected.floorEntry(start);
      long rate = initial == null || initial.getValue() == null ? 0 : initial.getValue().getMemorySize();
      for (var e : expected.subMap(start, false, absolute, false).entrySet()) {
        total += rate * (e.getKey() - start);
        start = e.getKey();
        rate = e.getValue() == null ? 0 : e.getValue().getMemorySize();
      }
      total += rate * (absolute - start);
      assertEquals(total, engines.get(Mode.EVENT).policy.valueAt(t));
      assertEquals(total, engines.get(Mode.BRIDGE).policy.valueAt(t));
    }
  }
}
