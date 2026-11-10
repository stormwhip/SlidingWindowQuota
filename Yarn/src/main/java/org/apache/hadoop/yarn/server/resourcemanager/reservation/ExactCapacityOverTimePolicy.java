package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Iterator;
import java.util.Map;
import java.util.NavigableMap;

import org.apache.hadoop.yarn.api.records.ReservationId;
import org.apache.hadoop.yarn.api.records.Resource;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningException;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningQuotaException;
import org.apache.hadoop.yarn.server.resourcemanager.scheduler.capacity.QueuePath;
import org.apache.hadoop.yarn.util.resource.ResourceUtils;
import org.apache.hadoop.yarn.util.resource.Resources;

/** Strict sliding-window quota policy with the existing YARN instantaneous checks. */
public final class ExactCapacityOverTimePolicy extends NoOverCommitPolicy {
  private final CapacityOverTimePolicy instantaneous = new CapacityOverTimePolicy();
  private long window;
  private float average;

  @Override
  public void init(String queue, ReservationSchedulerConfiguration conf) {
    instantaneous.init(queue, conf);
    var path = new QueuePath(queue);
    window = conf.getReservationWindow(path);
    average = conf.getAverageCapacity(path) / 100;
    if (window <= 0 || !Float.isFinite(average) || average < 0) {
      throw new IllegalArgumentException("Invalid window or average quota");
    }
  }

  @Override
  public long getValidWindow() {
    return window;
  }

  @Override
  public RLESparseResourceAllocation availableResources(RLESparseResourceAllocation available,
      Plan plan, String user, ReservationId id, long start, long end) throws PlanningException {
    return instantaneous.availableResources(available, plan, user, id, start, end);
  }

  static final String INTEGRAL_MESSAGE = "Exact integral quota exceeded";

  private static PlanningQuotaException overQuota(long time, int dimension) {
    return new PlanningQuotaException(INTEGRAL_MESSAGE + " at " + time + " for "
        + ResourceUtils.getResourceTypesArray()[dimension].getName());
  }

  @Override
  public void validate(Plan plan, ReservationAllocation reservation) throws PlanningException {
    try {
      super.validate(plan, reservation);
    } catch (PlanningException rejected) {
      throw new PlanningQuotaException(rejected);
    }
    var old = plan.getReservationById(reservation.getReservationId());
    if (old != null && !old.getUser().equals(reservation.getUser())) {
      throw new PlanningException("Replacement must keep the reservation owner");
    }
    Resource quota = Resources.multiply(plan.getTotalCapacity(), average);
    try {
      if (reservation.getPeriodicity() == 0) {
        check(plan, old, reservation, quota, Math.max(0, reservation.getStartTime() - window),
            Math.addExact(reservation.getEndTime(), window));
      } else {
        checkRecurring(plan, old, reservation, quota);
      }
    } catch (ArithmeticException overflow) {
      throw new PlanningException("Unrepresentable reservation time range", overflow);
    }
  }

  private record Domain(long first, long last) {}

  private void checkRecurring(Plan plan, ReservationAllocation old,
      ReservationAllocation reservation, Resource quota) throws PlanningException {
    long period = plan.getMaximumPeriodicity();
    if (period <= 0 || period % reservation.getPeriodicity() != 0) {
      throw new PlanningException("Reservation period must divide the plan period");
    }
    var domains = new ArrayList<Domain>();
    long anchor = window;
    // All windows influenced by a finite reservation, plus one pure recurring cycle.
    for (var current : plan.getAllReservations()) {
      if (current.getPeriodicity() == 0 && reservation.getUser().equals(current.getUser())
          && !reservation.getReservationId().equals(current.getReservationId())) {
        long tail = Math.addExact(current.getEndTime(), window);
        domains.add(new Domain(Math.max(0, current.getStartTime()), tail));
        anchor = Math.max(anchor, tail);
      }
    }
    domains.add(new Domain(anchor, Math.addExact(anchor, period)));
    domains.sort(Comparator.comparingLong(Domain::first));
    Domain pending = domains.get(0);
    for (int i = 1; i < domains.size(); i++) {
      Domain next = domains.get(i);
      if (next.first <= pending.last) {
        pending = new Domain(pending.first, Math.max(pending.last, next.last));
      } else {
        check(plan, old, reservation, quota, pending.first, pending.last);
        pending = next;
      }
    }
    check(plan, old, reservation, quota, pending.first, pending.last);
  }

  private void check(Plan plan, ReservationAllocation old, ReservationAllocation next,
      Resource quota, long first, long last) throws PlanningException {
    long support = Math.max(0, first - window);
    var history = plan.getConsumptionForUserOverTime(next.getUser(), support, last).getCumulative();
    var removed = old == null ? null : old.getResourcesOverTime(support, last).getCumulative();
    var added = next.getResourcesOverTime(support, last).getCumulative();
    long leftEnd = Math.subtractExact(last, window);
    var right = new Edge(history, removed, added, support, last);
    long leftStart = first - window;
    var left = new Edge(history, removed, added, leftStart, leftEnd);
    var integral = new Integral(quota, window);
    long t = support;
    for (long x = right.nextOr(last); x <= first && x < last; x = right.nextOr(last)) {
      integral.advance(right, null, x - t);
      right.step(x);
      t = x;
    }
    integral.advance(right, null, first - t);
    integral.check(first);
    for (t = first; t < last;) {
      long r = right.nextOr(last);
      long l = left.nextOr(leftEnd);
      long shifted = Math.addExact(l, window);
      long x = Math.min(r, shifted);
      integral.advance(right, left, x - t);
      integral.check(x);
      if (x < last) {
        if (x == r) right.step(r);
        if (x == shifted) left.step(l);
      }
      t = x;
    }
  }

  private static final class Integral {
    final long[] sum, limit;
    final BigInteger[] largeSum, largeLimit;

    Integral(Resource quota, long window) {
      int count = ResourceUtils.getNumberOfCountableResourceTypes();
      sum = new long[count];
      limit = new long[count];
      largeSum = new BigInteger[count];
      largeLimit = new BigInteger[count];
      for (int d = 0; d < count; d++) {
        long q = quota.getResourceInformation(d).getValue();
        try {
          limit[d] = Math.multiplyExact(q, window);
        } catch (ArithmeticException overflow) {
          largeLimit[d] = BigInteger.valueOf(q).multiply(BigInteger.valueOf(window));
        }
      }
    }

    void advance(Edge right, Edge left, long duration) {
      if (duration == 0) return;
      for (int d = 0; d < sum.length; d++) {
        if (largeSum[d] == null) {
          try {
            long slope = Math.subtractExact(right.rate(d), left == null ? 0 : left.rate(d));
            sum[d] = Math.addExact(sum[d], Math.multiplyExact(slope, duration));
            continue;
          } catch (ArithmeticException overflow) {
            largeSum[d] = BigInteger.valueOf(sum[d]);
          }
        }
        var slope = right.largeRate(d)
            .subtract(left == null ? BigInteger.ZERO : left.largeRate(d));
        largeSum[d] = largeSum[d].add(slope.multiply(BigInteger.valueOf(duration)));
      }
    }

    void check(long time) throws PlanningQuotaException {
      for (int d = 0; d < sum.length; d++) {
        if (largeSum[d] == null && largeLimit[d] == null) {
          if (sum[d] > limit[d]) throw overQuota(time, d);
        } else {
          var a = largeSum[d] == null ? BigInteger.valueOf(sum[d]) : largeSum[d];
          var b = largeLimit[d] == null ? BigInteger.valueOf(limit[d]) : largeLimit[d];
          if (a.compareTo(b) > 0) throw overQuota(time, d);
        }
      }
    }
  }

  private static final class Edge {
    final Cursor history, removed, added;

    Edge(NavigableMap<Long, Resource> history, NavigableMap<Long, Resource> removed,
        NavigableMap<Long, Resource> added, long start, long end) {
      this.history = new Cursor(history, start, end);
      this.removed = removed == null ? null : new Cursor(removed, start, end);
      this.added = new Cursor(added, start, end);
    }

    long rate(int d) {
      return Math.addExact(Math.subtractExact(history.rate(d),
          removed == null ? 0 : removed.rate(d)), added.rate(d));
    }

    BigInteger largeRate(int d) {
      return BigInteger.valueOf(history.rate(d))
          .subtract(BigInteger.valueOf(removed == null ? 0 : removed.rate(d)))
          .add(BigInteger.valueOf(added.rate(d)));
    }

    long nextOr(long end) {
      long time = Math.min(history.nextOr(end), added.nextOr(end));
      return removed == null ? time : Math.min(time, removed.nextOr(end));
    }

    void step(long t) {
      history.step(t);
      if (removed != null) removed.step(t);
      added.step(t);
    }
  }

  private static final class Cursor {
    final Iterator<Map.Entry<Long, Resource>> points;
    Map.Entry<Long, Resource> next;
    Resource resource;

    Cursor(NavigableMap<Long, Resource> map, long start, long end) {
      var first = map.floorEntry(start);
      resource = first == null ? null : first.getValue();
      points = map.subMap(start, false, end, false).entrySet().iterator();
      next = points.hasNext() ? points.next() : null;
    }

    long rate(int d) {
      return resource == null ? 0 : resource.getResourceInformation(d).getValue();
    }

    long nextOr(long end) {
      return next == null ? end : next.getKey();
    }

    void step(long t) {
      if (next != null && next.getKey() == t) {
        resource = next.getValue();
        next = points.hasNext() ? points.next() : null;
      }
    }
  }
}
