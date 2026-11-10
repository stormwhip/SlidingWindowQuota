package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.util.ArrayList;
import java.util.NavigableMap;
import java.util.TreeMap;

import org.apache.hadoop.yarn.api.records.Resource;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningException;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class QuotaMigrationTest {
  @Test
  void permitsPartialRepairOfAReservationAcceptedByLegacyYarn() throws PlanningException {
    long base = 1_791_808_200_000L, w = 4_000, cap = 48_000;
    var fixture = PolicyProbeTest.fixture(w, 12, 1000);
    var plan = fixture.plan();
    var old = PolicyProbeTest.flat(base + 4_000, base + 8_000, 10);
    assertTrue(plan.addReservation(PolicyProbeTest.flat(base + 2_000, base + 4_000, 20), false));
    assertTrue(plan.addReservation(old, false));
    for (int demand : new int[]{4, 9, 11}) {
      var replacement = PolicyProbeTest.flat(base + 4_000, base + 8_000, demand);
      var comparison = ExactRleWindow.prepare(plan, old, replacement, w, base, base + 12_000);
      assertEquals((40L + 2L * demand) * 1000, comparison.evaluate(Long.MAX_VALUE).memoryTime());
      assertEquals(demand <= 10, comparison.nonWorsening(cap));
      assertSame(old, plan.getReservationById(old.getReservationId()));
    }
  }

  @Test
  void detectsAnInteriorViolationEvenWhenBothEndsAndGlobalPeaksPass() {
    for (long unit : new long[]{1, 10_000_000_000L}) {
      var old = new TreeMap<Long, Resource>();
      old.put(2L, memory(unit));
      old.put(4L, memory(2 * unit));
      old.put(6L, memory(0));
      var next = new TreeMap<Long, Resource>();
      next.put(2L, memory(2 * unit));
      next.put(4L, memory(unit));
      next.put(6L, memory(0));
      var restricted = new ExactRleWindow.Prepared(old, old, next, 4, 4, 6);
      assertEquals(6 * unit, restricted.evaluate(Long.MAX_VALUE).memoryTime());
      assertEquals(6 * unit, new ExactRleWindow.Prepared(old, null, new TreeMap<>(), 4, 0, 10)
          .evaluate(Long.MAX_VALUE).memoryTime());
      assertFalse(restricted.nonWorsening(4 * unit));
      assertTrue(new ExactRleWindow.Prepared(next, next, old, 4, 4, 6).nonWorsening(4 * unit));
    }
  }

  @Test
  void exhaustiveAgreementWithIndependentHalfTickIntegration() {
    var profiles = new ArrayList<int[]>();
    var maps = new ArrayList<NavigableMap<Long, Resource>>();
    for (int code = 0; code < 81; code++) {
      int[] load = new int[4];
      var map = new TreeMap<Long, Resource>();
      int digits = code;
      for (int t = 0; t < 4; t++) {
        load[t] = digits % 3;
        digits /= 3;
        if (t == 0 || load[t] != load[t - 1]) map.put((long) t, memory(load[t]));
      }
      map.put(4L, memory(0));
      profiles.add(load);
      maps.add(map);
    }
    int total = 0, permitsPartial = 0, endpointMisses = 0;
    for (int w = 1; w <= 4; w++) {
      for (int i = 0; i < 81; i++) {
        int[] before = halfTickIntegrals(profiles.get(i), w);
        for (int j = 0; j < 81; j++) {
          int[] after = halfTickIntegrals(profiles.get(j), w);
          var candidate = new ExactRleWindow.Prepared(maps.get(i), maps.get(i), maps.get(j),
              w, 0, w + 4);
          for (int q = 1; q <= 4; q++) {
            boolean expected = true, strict = true, integerOnly = true;
            for (int k = 0; k < after.length; k++) {
              if (after[k] > 2 * q) strict = false;
              if (after[k] > Math.max(2 * q, before[k])) {
                expected = false;
                if (k % 2 == 0) integerOnly = false;
              }
            }
            assertEquals(expected, candidate.nonWorsening(q));
            if (expected && !strict) permitsPartial++;
            if (!expected && integerOnly) endpointMisses++;
            total++;
          }
        }
      }
    }
    assertEquals(104_976, total);
    assertTrue(permitsPartial > 0);
    assertTrue(endpointMisses > 0);
    System.out.println("Migration cases: " + total + ", allowed beyond strict quota: "
        + permitsPartial + ", violations missed at all integer endpoints: " + endpointMisses);
  }

  private static int[] halfTickIntegrals(int[] load, int window) {
    int[] sums = new int[2 * (load.length + window) + 1];
    for (int end = 0; end < sums.length; end++) {
      for (int i = Math.max(0, end - 2 * window); i < Math.min(end, 2 * load.length); i++) {
        sums[end] += load[i / 2];
      }
    }
    return sums;
  }

  private static Resource memory(long value) {
    return Resource.newInstance(value, 0);
  }
}
