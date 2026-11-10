package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Random;
import java.util.TreeMap;

import org.apache.hadoop.yarn.api.records.Resource;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class EventScalingTest {
  private static final long SECOND = 1_000;
  private static final long WINDOW = 24 * 3_600 * SECOND;
  private static final long DURATION = 4_096 * SECOND;

  @Test
  void timeAcceptedPolicyCalls() throws Exception {
    String output = System.getProperty("quota.eventScalingOutput");
    if (output == null) return;

    int fork = Integer.getInteger("quota.eventScalingFork", 1);
    var random = new Random(84109L + fork);
    var sizes = new ArrayList<>(List.of(64, 128, 256, 512, 1024, 2048, 4096));
    Collections.shuffle(sizes, random);
    var rows = new ArrayList<String>();
    rows.add("fork,history_events,pair,sampled_ns,exact_ns");
    for (int segments : sizes) {
      var fixture = PolicyProbeTest.fixture(WINDOW, 25, SECOND);
      long start = 2 * WINDOW;
      long end = start + DURATION;
      long step = DURATION / segments;
      var profile = new TreeMap<ReservationInterval, Resource>();
      for (int i = 0; i < segments; i++) {
        long left = start + i * step;
        profile.put(new ReservationInterval(left, left + step),
            Resource.newInstance(1 + i % 4, 1));
      }
      var history = PolicyProbeTest.allocation(start, end, profile);
      assertTrue(fixture.plan().addReservation(history, true));
      var candidate = PolicyProbeTest.flat(start, end, 8);
      long events = fixture.plan().getConsumptionForUserOverTime(
          candidate.getUser(), start - WINDOW, end + WINDOW).getCumulative().keySet()
          .stream().filter(t -> t >= start && t <= end).count();
      assertEquals(segments + 1, events);

      String queue = ReservationSystemTestUtil.getFullReservationQueueName();
      var exact = new ExactCapacityOverTimePolicy();
      exact.init(queue, ReservationSystemTestUtil.createConf(queue, WINDOW, 100, 25));
      SharingPolicy[] policies = {fixture.policy(), exact};
      long[] times = new long[2];
      for (int pair = -150; pair < 200; pair++) {
        int first = random.nextInt(2);
        for (int pass = 0; pass < 2; pass++) {
          int mode = first ^ pass;
          long before = System.nanoTime();
          policies[mode].validate(fixture.plan(), candidate);
          times[mode] = System.nanoTime() - before;
        }
        if (pair >= 0) {
          rows.add(fork + "," + events + "," + pair + "," + times[0] + "," + times[1]);
        }
      }
    }
    Files.write(Path.of(output), rows);
  }
}
