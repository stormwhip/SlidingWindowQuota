package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.util.Map;

import org.apache.hadoop.yarn.api.records.ReservationId;
import org.apache.hadoop.yarn.api.records.Resource;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningException;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningQuotaException;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.planning.ReservationAgent;
import org.apache.hadoop.yarn.server.resourcemanager.scheduler.QueueMetrics;
import org.apache.hadoop.yarn.util.resource.DefaultResourceCalculator;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.mock;

class ExactCapacityOverTimePolicyTest {
  static final String QUEUE = ReservationSystemTestUtil.getFullReservationQueueName();
  static final Resource MIN = Resource.newInstance(1, 1);
  static final long HOUR = 3_600_000, DAY = 24 * HOUR, BASE = 1_791_763_200_000L;

  static InMemoryPlan plan(SharingPolicy policy, long window, int percent, Resource total,
      QueueMetrics metrics, ReservationAgent agent) {
    policy.init(QUEUE, ReservationSystemTestUtil.createConf(QUEUE, window, 100, percent));
    return new InMemoryPlan(metrics, policy, agent, total, 1000, new DefaultResourceCalculator(),
        MIN, total, QUEUE, null, true, ReservationSystemTestUtil.createMockRMContext());
  }

  private static InMemoryPlan plan(long window, Resource total) {
    return plan(new ExactCapacityOverTimePolicy(), window, 25, total, mock(QueueMetrics.class),
        mock(ReservationAgent.class));
  }

  static InMemoryReservationAllocation flat(ReservationId id, String user, long start, long end,
      long memory, int cpu, long period) {
    var definition = ReservationSystemTestUtil.createSimpleReservationDefinition(
        start, end, end - start, 1, period == 0 ? null : Long.toString(period));
    return new InMemoryReservationAllocation(id, definition, user, QUEUE, start, end,
        Map.of(new ReservationInterval(start, end), Resource.newInstance(memory, cpu)),
        new DefaultResourceCalculator(), MIN);
  }

  @Test
  void cpuQuotaAndReplacementAreCheckedBeforeWriteback() throws PlanningException {
    var plan = plan(DAY, Resource.newInstance(100, 100));
    var id = ReservationSystemTestUtil.getNewReservationId();
    assertTrue(plan.addReservation(flat(id, "u", BASE, BASE + DAY, 10, 20, 0), false));
    var legal = flat(id, "u", BASE, BASE + DAY, 10, 22, 0);
    assertTrue(plan.updateReservation(legal));
    var illegal = flat(id, "u", BASE, BASE + DAY, 10, 30, 0);
    var rejected = assertThrows(PlanningQuotaException.class,
        () -> plan.updateReservation(illegal));
    assertTrue(rejected.getMessage().contains("vcores"));
    assertSame(legal, plan.getReservationById(id));
  }

  @Test
  void recurringRequestChecksOverlapWithFiniteReservationsInLaterCycles()
      throws PlanningException {
    var plan = plan(4 * HOUR, Resource.newInstance(100, 100));
    long future = BASE - Math.floorMod(BASE, DAY) + 10 * DAY;
    assertTrue(plan.addReservation(flat(ReservationSystemTestUtil.getNewReservationId(), "u",
        future, future + 4 * HOUR, 20, 20, 0), false));
    var rejected = assertThrows(PlanningQuotaException.class,
        () -> plan.addReservation(flat(ReservationSystemTestUtil.getNewReservationId(), "u",
            0, 4 * HOUR, 10, 10, DAY), false));
    assertTrue(rejected.getMessage().startsWith(ExactCapacityOverTimePolicy.INTEGRAL_MESSAGE));
    assertTrue(plan.addReservation(flat(ReservationSystemTestUtil.getNewReservationId(), "u",
        0, 4 * HOUR, 4, 4, DAY), false));
  }

  @Test
  void recurringOnlyPlanChecksAWindowLongerThanThePeriod() throws PlanningException {
    var plan = plan(DAY + HOUR, Resource.newInstance(100, 100));
    // A 25-hour window can cover seven hours of one occurrence and one of the next.
    var over = flat(ReservationSystemTestUtil.getNewReservationId(), "u",
        17 * HOUR, DAY, 80, 1, DAY);
    var rejected = assertThrows(PlanningQuotaException.class,
        () -> plan.addReservation(over, false));
    assertTrue(rejected.getMessage().startsWith(ExactCapacityOverTimePolicy.INTEGRAL_MESSAGE));
    var fits = flat(ReservationSystemTestUtil.getNewReservationId(), "u",
        17 * HOUR, DAY, 78, 1, DAY);
    assertTrue(plan.addReservation(fits, false));
  }

  @Test
  void productsBeyondLongRangeRemainExact() throws PlanningException {
    long capacity = 1L << 50;
    var plan = plan(DAY, Resource.newInstance(capacity, 100));
    var id = ReservationSystemTestUtil.getNewReservationId();
    assertTrue(plan.addReservation(flat(id, "u", BASE, BASE + DAY, capacity / 4, 1, 0), false));
    var rejected = assertThrows(PlanningQuotaException.class,
        () -> plan.updateReservation(flat(id, "u", BASE, BASE + DAY, capacity / 4 + 1, 1, 0)));
    assertTrue(rejected.getMessage().startsWith(ExactCapacityOverTimePolicy.INTEGRAL_MESSAGE));
  }
}
