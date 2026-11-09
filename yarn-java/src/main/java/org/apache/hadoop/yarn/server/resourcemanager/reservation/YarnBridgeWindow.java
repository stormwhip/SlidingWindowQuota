package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.util.TreeMap;

import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningException;

import static org.apache.hadoop.yarn.server.resourcemanager.reservation.QuotaChanges.*;

/** Snapshot adapter for comparing the bridge index against YARN's exact RLE sweep. */
final class YarnBridgeWindow {
  private YarnBridgeWindow() {}

  static ExactRleWindow.Peak proposed(Plan plan, ReservationAllocation candidate,
      long window, long first, long last) throws PlanningException {
    if (window <= 0 || first > last) throw new IllegalArgumentException("Invalid window range");
    long start = Math.subtractExact(first, window);
    long end = Math.subtractExact(last, first);
    var history = plan.getConsumptionForUserOverTime(candidate.getUser(), start, last);
    var old = plan.getReservationById(candidate.getReservationId());
    if (old != null && !old.getUser().equals(candidate.getUser())) {
      throw new PlanningException("Replacement must keep the reservation owner");
    }
    var base = new TreeMap<Long, Long>();
    collect(base, history, start, first, last, window, 1);
    var change = new TreeMap<Long, Long>();
    if (old != null) {
      collect(change, old.getResourcesOverTime(start, last), start, first, last, window, -1);
    }
    collect(change, candidate.getResourcesOverTime(start, last), start, first, last, window, 1);
    var tree = new BridgeQuotaIndex(base, end);
    Peak peak;
    if (end == 0) {
      long value = tree.valueAt(0);
      for (var entry : change.headMap(0L, false).entrySet()) {
        value = Math.addExact(value, Math.multiplyExact(-entry.getKey(), entry.getValue()));
      }
      peak = new Peak(value, 0);
    } else {
      peak = new Peak(Long.MIN_VALUE, 0);
      long previous = 0;
      for (Piece piece : pieces(change, end)) {
        if (previous < piece.lo()) {
          Peak gap = tree.peak(new Piece(previous, piece.lo(), 0, 0));
          peak = peak.include(gap.value(), gap.time());
        }
        Peak changed = tree.peak(piece);
        peak = peak.include(changed.value(), changed.time());
        previous = piece.hi();
      }
      if (previous < end) {
        Peak tail = tree.peak(new Piece(previous, end, 0, 0));
        peak = peak.include(tail.value(), tail.time());
      }
    }
    return new ExactRleWindow.Peak(peak.value(), Math.addExact(first, peak.time()));
  }

  private static void collect(TreeMap<Long, Long> jumps, RLESparseResourceAllocation rle,
      long start, long origin, long end, long window, long sign) {
    var points = rle.getCumulative();
    var initial = points.floorEntry(start);
    long rate = initial == null || initial.getValue() == null
        ? 0 : Math.multiplyExact(sign, initial.getValue().getMemorySize());
    long t = start;
    for (var entry : points.subMap(start, false, end, false).entrySet()) {
      append(jumps, t, entry.getKey(), rate, origin, window);
      t = entry.getKey();
      rate = entry.getValue() == null ? 0 : Math.multiplyExact(sign, entry.getValue().getMemorySize());
    }
    append(jumps, t, end, rate, origin, window);
  }

  private static void append(TreeMap<Long, Long> jumps, long start, long end,
      long rate, long origin, long window) {
    addReservation(jumps, new Reservation(Math.subtractExact(start, origin),
        Math.subtractExact(end, origin), rate), window);
  }
}
