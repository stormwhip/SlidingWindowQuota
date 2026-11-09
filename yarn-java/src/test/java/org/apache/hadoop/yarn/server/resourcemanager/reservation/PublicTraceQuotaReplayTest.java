package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.SplittableRandom;
import java.util.TreeMap;

import org.apache.hadoop.yarn.api.records.Resource;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningException;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningQuotaException;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.planning.ReservationAgent;
import org.apache.hadoop.yarn.server.resourcemanager.scheduler.QueueMetrics;
import org.junit.jupiter.api.Test;

import static org.apache.hadoop.yarn.server.resourcemanager.reservation.ExactCapacityOverTimePolicyTest.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.mock;

class PublicTraceQuotaReplayTest {
  private enum Decision { ACCEPT, INTEGRAL, INSTANT }

  private record Job(String dataset, int row, String user, long submit, long start,
      long end, int cores, int capacity, boolean measured) {}

  private record Outcome(Decision decision, long nanos) {}

  @Test
  void compareSamePlanStateThroughWriteback() throws IOException, PlanningException {
    String output = System.getProperty("quota.publicTraceOutput");
    if (output == null) return;
    var input = Path.of(System.getProperty("quota.publicTraceInput", "../results/public-trace/requests.csv"));
    List<Job> jobs = Files.readAllLines(input).stream().skip(1).map(PublicTraceQuotaReplayTest::job).toList();
    var rows = new ArrayList<String>();
    rows.add("dataset,row,quota_percent,measured,history_changes,legacy,exact,legacy_ns,exact_ns");
    var metrics = mock(QueueMetrics.class);
    var agent = mock(ReservationAgent.class);
    var order = new SplittableRandom(62114);
    int[] quotas = quotas();
    for (String dataset : List.of("kth", "cerit")) {
      var trace = jobs.stream().filter(j -> j.dataset.equals(dataset)).toList();
      assertEquals(1450, trace.size());
      int cores = trace.get(0).capacity;
      var capacity = Resource.newInstance(1024L * cores, cores);
      for (int quota : quotas) {
        var legacy = plan(new CapacityOverTimePolicy(), DAY, quota, capacity, metrics, agent);
        var exact = plan(new ExactCapacityOverTimePolicy(), DAY, quota, capacity, metrics, agent);
        for (Job j : trace) {
          if (j.row % 32 == 0) {
            legacy.archiveCompletedReservations(j.submit);
            exact.archiveCompletedReservations(j.submit);
          }
          var id = ReservationSystemTestUtil.getNewReservationId();
          var a = flat(id, j.user, j.start, j.end, 1024L * j.cores, j.cores, 0);
          var b = flat(id, j.user, j.start, j.end, 1024L * j.cores, j.cores, 0);
          long from = j.start - 2 * DAY, to = j.end + DAY;
          var history = profile(exact, j.user, from, to);
          assertEquals(history, profile(legacy, j.user, from, to), dataset + " state " + j.row);
          int h = history.tailMap(from, false).size();
          Outcome original;
          Outcome proposed;
          if (order.nextBoolean()) {
            original = write(legacy, a);
            proposed = write(exact, b);
          } else {
            proposed = write(exact, b);
            original = write(legacy, a);
          }
          assertEquals(original.decision == Decision.INSTANT, proposed.decision == Decision.INSTANT,
              dataset + " instantaneous " + j.row);
          // Match the exact plan before the next pair. Cleanup is outside both clocks.
          if (original.decision != proposed.decision) {
            if (proposed.decision == Decision.ACCEPT) assertTrue(legacy.addReservation(a, true));
            else if (original.decision == Decision.ACCEPT) assertTrue(legacy.deleteReservation(id));
          }
          assertEquals(proposed.decision == Decision.ACCEPT, legacy.getReservationById(id) != null);
          rows.add(dataset + "," + j.row + "," + quota + "," + j.measured + "," + h + ","
              + original.decision + "," + proposed.decision + "," + original.nanos + "," + proposed.nanos);
        }
      }
    }
    assertEquals(1 + 1450 * 2 * quotas.length, rows.size());
    Files.write(Path.of(output), rows);
  }

  private static int[] quotas() {
    String raw = System.getProperty("quota.publicTraceQuotas");
    if (raw == null || raw.isBlank()) {
      return new int[]{10, 25};
    }
    return java.util.Arrays.stream(raw.split(",")).map(String::trim)
        .filter(s -> !s.isEmpty()).mapToInt(Integer::parseInt).toArray();
  }

  private static Job job(String row) {
    String[] s = row.split(",");
    long submit = BASE + 1000 * Long.parseLong(s[3]);
    long start = BASE + 1000 * Long.parseLong(s[4]);
    return new Job(s[0], Integer.parseInt(s[1]), "u" + s[2], submit, start,
        start + 1000 * Long.parseLong(s[5]), Integer.parseInt(s[6]), Integer.parseInt(s[7]), s[8].equals("1"));
  }

  private static TreeMap<Long, Long> profile(Plan plan, String user, long from, long to) {
    var points = plan.getConsumptionForUserOverTime(user, from, to).getCumulative();
    var start = points.floorEntry(from);
    long rate = start == null || start.getValue() == null ? 0 : start.getValue().getMemorySize();
    var result = new TreeMap<Long, Long>();
    result.put(from, rate);
    for (var point : points.subMap(from, false, to, false).entrySet()) {
      long next = point.getValue() == null ? 0 : point.getValue().getMemorySize();
      if (next != rate) {
        result.put(point.getKey(), next);
        rate = next;
      }
    }
    return result;
  }

  @Test
  void continuousWorldsDiverge() throws IOException, PlanningException {
    String output = System.getProperty("quota.worldsOutput");
    if (output == null) return;
    var input = Path.of(System.getProperty("quota.publicTraceInput", "../results/public-trace/requests.csv"));
    List<Job> jobs = Files.readAllLines(input).stream().skip(1).map(PublicTraceQuotaReplayTest::job).toList();
    var rows = new ArrayList<String>();
    rows.add("dataset,row,quota_percent,legacy,exact");
    var metrics = mock(QueueMetrics.class);
    var agent = mock(ReservationAgent.class);
    int[] quotas = quotas();
    for (String dataset : List.of("kth", "cerit")) {
      var trace = jobs.stream().filter(j -> j.dataset.equals(dataset)).toList();
      assertEquals(1450, trace.size());
      int cores = trace.get(0).capacity;
      var capacity = Resource.newInstance(1024L * cores, cores);
      for (int quota : quotas) {
        var legacy = plan(new CapacityOverTimePolicy(), DAY, quota, capacity, metrics, agent);
        var exact = plan(new ExactCapacityOverTimePolicy(), DAY, quota, capacity, metrics, agent);
        for (Job j : trace) {
          var id = ReservationSystemTestUtil.getNewReservationId();
          var a = flat(id, j.user, j.start, j.end, 1024L * j.cores, j.cores, 0);
          var b = flat(id, j.user, j.start, j.end, 1024L * j.cores, j.cores, 0);
          var legacyDecision = write(legacy, a).decision;
          var exactDecision = write(exact, b).decision;
          rows.add(dataset + "," + j.row + "," + quota + "," + legacyDecision + "," + exactDecision);
        }
      }
    }
    assertEquals(1 + 1450 * 2 * quotas.length, rows.size());
    Files.write(Path.of(output), rows);
  }

  private static Outcome write(InMemoryPlan plan, InMemoryReservationAllocation request)
      throws PlanningException {
    long before = System.nanoTime();
    try {
      boolean written = plan.addReservation(request, false);
      long elapsed = System.nanoTime() - before;
      assertTrue(written);
      return new Outcome(Decision.ACCEPT, elapsed);
    } catch (PlanningQuotaException denied) {
      long elapsed = System.nanoTime() - before;
      String message = denied.getMessage();
      boolean integral = message != null && (message.startsWith("Integral (avg")
          || message.startsWith(ExactCapacityOverTimePolicy.INTEGRAL_MESSAGE));
      return new Outcome(integral ? Decision.INTEGRAL : Decision.INSTANT, elapsed);
    }
  }
}
