package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import org.apache.hadoop.yarn.api.records.ReservationDefinition;
import org.apache.hadoop.yarn.api.records.Resource;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningException;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningQuotaException;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.planning.ReservationAgent;
import org.apache.hadoop.yarn.server.resourcemanager.scheduler.QueueMetrics;
import org.apache.hadoop.yarn.util.resource.DefaultResourceCalculator;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;

class PolicyProbeTest {
  private static final long SECOND = 1000;
  private static final long START = 1_791_808_200_000L;
  private static final Resource MIN = Resource.newInstance(1, 1);
  private static final Resource TOTAL = Resource.newInstance(100, 100);

  record Fixture(CapacityOverTimePolicy policy, InMemoryPlan plan) {}

  private static Fixture fixture(long window, int averagePercent) {
    return fixture(window, averagePercent, SECOND);
  }

  static Fixture fixture(long window, int averagePercent, long step) {
    String queue = ReservationSystemTestUtil.getFullReservationQueueName();
    CapacityOverTimePolicy policy = new CapacityOverTimePolicy();
    policy.init(queue, ReservationSystemTestUtil.createConf(queue, window, 100, averagePercent));
    InMemoryPlan plan = new InMemoryPlan(mock(QueueMetrics.class), policy,
        mock(ReservationAgent.class), TOTAL, step, new DefaultResourceCalculator(),
        MIN, TOTAL, queue, null, true, ReservationSystemTestUtil.createMockRMContext());
    return new Fixture(policy, plan);
  }

  static InMemoryReservationAllocation allocation(long start, long end,
      Map<ReservationInterval, Resource> profile) {
    ReservationDefinition def = ReservationSystemTestUtil.createSimpleReservationDefinition(
        start, end, end - start, 1, null);
    return new InMemoryReservationAllocation(ReservationSystemTestUtil.getNewReservationId(),
        def, "probe", ReservationSystemTestUtil.getFullReservationQueueName(),
        start, end, profile, new DefaultResourceCalculator(), MIN);
  }

  static InMemoryReservationAllocation flat(long start, long end, int memory) {
    return allocation(start, end, Map.of(new ReservationInterval(start, end),
        Resource.newInstance(memory, 1)));
  }

  @Test
  void roundingDiffersFromExactIntegralAtQuotaBoundary() {
    Fixture f = fixture(4 * SECOND, 10);
    var slightlyOver = flat(START, START + SECOND, 41);
    var roundsUp = flat(START, START + SECOND, 42);
    long exactQuota = 10 * 4 * SECOND;
    assertTrue(41 * SECOND > exactQuota);
    assertDoesNotThrow(() -> f.policy().validate(f.plan(), slightlyOver));
    assertThrows(PlanningQuotaException.class, () -> f.policy().validate(f.plan(), roundsUp));
  }

  @Test
  void benchmarkPolicyEntry() throws PlanningException, IOException {
    String output = System.getProperty("quota.timingFile");
    if (output == null) return;
    List<String> rows = new ArrayList<>();
    rows.add("background_segments,expected_accept,repeat,validate_ns");
    for (int segments : new int[]{128, 1024}) {
      Fixture f = fixture(32 * SECOND, 25);
      Map<ReservationInterval, Resource> background = new TreeMap<>();
      for (int i = 0; i < segments; ++i) {
        long start = START + i * SECOND;
        background.put(new ReservationInterval(start, start + SECOND),
            Resource.newInstance(1 + (i % 4), 1));
      }
      assertTrue(f.plan().addReservation(allocation(START, START + segments * SECOND,
          background), true));
      long left = START + 16 * SECOND, right = left + (segments / 2) * SECOND;
      var valid = flat(left, right, 8);
      var invalid = flat(left, right, 30);
      for (int repeat = -80; repeat < 120; ++repeat) {
        for (int step = 0; step < 2; ++step) {
          boolean expected = ((repeat + step) & 1) == 0;
          var candidate = expected ? valid : invalid;
          boolean actual = true;
          long started = System.nanoTime();
          try {
            f.policy().validate(f.plan(), candidate);
          } catch (PlanningQuotaException rejected) {
            actual = false;
          }
          long elapsed = System.nanoTime() - started;
          assertEquals(expected, actual);
          if (repeat >= 0) rows.add(segments + "," + expected + "," + repeat + "," + elapsed);
        }
      }
    }
    Files.write(Path.of(output), rows);
  }
}
