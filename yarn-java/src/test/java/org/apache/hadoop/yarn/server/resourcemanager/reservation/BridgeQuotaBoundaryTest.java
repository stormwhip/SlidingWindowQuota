package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.util.TreeMap;

import org.junit.jupiter.api.Test;

import static org.apache.hadoop.yarn.server.resourcemanager.reservation.QuotaChanges.*;
import static org.junit.jupiter.api.Assertions.assertEquals;

class BridgeQuotaBoundaryTest {
  @Test
  void wideCoordinatesAndReservationStillActiveAtDomainEnd() {
    long unit = 1_000_000_000L;
    long window = 32 * unit, end = 96 * unit;
    var background = new Reservation[] {
        new Reservation(-16 * unit, 6 * unit, 44),
        new Reservation(7 * unit, 24 * unit, 21),
        new Reservation(39 * unit, 58 * unit, 30),
        new Reservation(59 * unit, 79 * unit, 49),
        new Reservation(81 * unit, 96 * unit, 18)
    };
    var jumps = new TreeMap<Long, Long>();
    for (Reservation r : background) addReservation(jumps, r, window);
    var tree = new BridgeQuotaIndex(jumps, end);
    var events = new EventQuotaIndex(jumps);
    var domain = new Piece(0, end, 0, 0);
    var old = Reservation.NONE;
    for (Reservation next : new Reservation[] {
        new Reservation(75 * unit, 95 * unit, 55),
        new Reservation(77 * unit, 93 * unit, 33),
        Reservation.NONE
    }) {
      var change = between(old, next, window, end);
      tree.commit(change);
      events.commit(change);
      var best = new Peak(Long.MIN_VALUE, 0);
      for (long t = 0; t <= end; t += unit) {
        long value = next.demand() * next.overlap(t, window);
        for (Reservation r : background) value += r.demand() * r.overlap(t, window);
        assertEquals(value, tree.valueAt(t));
        assertEquals(value, events.valueAt(t));
        best = best.include(value, t);
      }
      assertEquals(best, tree.peak(domain));
      assertEquals(best, events.peak(domain));
      old = next;
    }
  }
}
