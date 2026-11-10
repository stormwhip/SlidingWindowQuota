package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.SplittableRandom;
import java.util.concurrent.TimeUnit;

import org.apache.hadoop.ipc.RPC;
import org.apache.hadoop.yarn.api.ApplicationClientProtocol;
import org.apache.hadoop.yarn.api.protocolrecords.GetNewReservationRequest;
import org.apache.hadoop.yarn.api.protocolrecords.ReservationDeleteRequest;
import org.apache.hadoop.yarn.api.protocolrecords.ReservationSubmissionRequest;
import org.apache.hadoop.yarn.api.protocolrecords.ReservationUpdateRequest;
import org.apache.hadoop.yarn.api.records.ReservationDefinition;
import org.apache.hadoop.yarn.api.records.ReservationId;
import org.apache.hadoop.yarn.api.records.ReservationRequest;
import org.apache.hadoop.yarn.api.records.ReservationRequestInterpreter;
import org.apache.hadoop.yarn.api.records.ReservationRequests;
import org.apache.hadoop.yarn.api.records.Resource;
import org.apache.hadoop.yarn.client.ClientRMProxy;
import org.apache.hadoop.yarn.conf.YarnConfiguration;
import org.apache.hadoop.yarn.exceptions.YarnException;
import org.apache.hadoop.yarn.nodelabels.NodeAttributeStore;
import org.apache.hadoop.yarn.server.resourcemanager.MockNM;
import org.apache.hadoop.yarn.server.resourcemanager.ResourceManager;
import org.apache.hadoop.yarn.server.resourcemanager.TestResourceTrackerService.NullNodeAttributeStore;
import org.apache.hadoop.yarn.server.resourcemanager.scheduler.ResourceScheduler;
import org.apache.hadoop.yarn.server.resourcemanager.scheduler.capacity.CapacityScheduler;
import org.apache.hadoop.yarn.server.resourcemanager.scheduler.capacity.CapacitySchedulerConfiguration;
import org.apache.hadoop.yarn.server.resourcemanager.scheduler.capacity.QueuePath;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

class ResourceManagerQuotaTest {
  private static final String LEGACY = "legacy";
  private static final String EXACT = "exact";
  private static final long MINUTE = 60_000, HOUR = 60 * MINUTE, DAY = 24 * HOUR;

  @Test
  @Timeout(60)
  void missedWindowReachesBothClientRpcEndpoints() throws Exception {
    try (var cluster = new Cluster()) {
      long start = nextStart();
      for (String queue : List.of(LEGACY, EXACT)) {
        Plan plan = cluster.plan(queue);
        cluster.submit(queue, cluster.newId(), definition(start, 12 * HOUR, 40));
        var late = definition(start + 12 * HOUR, DAY, 20);
        var newId = cluster.newId();
        if (queue.equals(EXACT)) {
          assertQuotaRejected(() -> cluster.submit(queue, newId, late));
          assertNull(plan.getReservationById(newId));
        } else {
          cluster.submit(queue, newId, late);
          assertNotNull(plan.getReservationById(newId));
          cluster.delete(newId);
        }

        var id = cluster.newId();
        var current = definition(start + 12 * HOUR, DAY, 5);
        cluster.submit(queue, id, current);
        if (queue.equals(EXACT)) {
          assertQuotaRejected(() -> cluster.update(id, late));
          assertEquals(5, plan.getReservationById(id)
              .getResourcesAtTime(current.getArrival()).getVirtualCores());
        } else {
          cluster.update(id, late);
          assertEquals(20, plan.getReservationById(id)
              .getResourcesAtTime(late.getArrival()).getVirtualCores());
        }
      }
    }
  }

  @Test
  @Timeout(60)
  void flexibleRequestHasALegalInteriorStartAfterTheDefaultAgentRejects() throws Exception {
    try (var cluster = new Cluster()) {
      long start = nextStart();
      cluster.submit(EXACT, cluster.newId(), definition(start, 12 * HOUR, 40));
      cluster.submit(EXACT, cluster.newId(), definition(start + 52 * HOUR, 12 * HOUR, 40));

      var request = definition(start + 12 * HOUR, DAY, 20);
      request.setDeadline(start + 52 * HOUR);
      var id = cluster.newId();
      assertQuotaRejected(() -> cluster.submit(EXACT, id, request));
      var plan = cluster.plan(EXACT);
      assertNull(plan.getReservationById(id));

      long earliest = start + 18 * HOUR;
      long step = plan.getStep();
      assertQuotaRejected(() -> cluster.submit(EXACT, id, definition(earliest - step, DAY, 20)));
      cluster.submit(EXACT, id, definition(earliest, DAY, 20));
      assertEquals(20, plan.getReservationById(id).getResourcesAtTime(earliest).getVirtualCores());
      cluster.delete(id);

      long latest = start + 22 * HOUR;
      var lastId = cluster.newId();
      assertQuotaRejected(() -> cluster.submit(EXACT, lastId, definition(latest + step, DAY, 20)));
      cluster.submit(EXACT, lastId, definition(latest, DAY, 20));
      assertEquals(20, plan.getReservationById(lastId).getResourcesAtTime(latest).getVirtualCores());
    }
  }

  @Test
  @Timeout(180)
  void measureAcceptedLoopbackRpc() throws Exception {
    String output = System.getProperty("quota.rmOutput");
    assumeTrue(output != null, "Loopback timings are opt-in");
    int warmup = Integer.getInteger("quota.rmWarmup", 150);
    int measured = Integer.getInteger("quota.rmMeasured", 400);
    var order = new SplittableRandom(53780 + Integer.getInteger("quota.rmFork", 1));
    var rows = new ArrayList<String>();
    rows.add("policy,operation,row,ns");
    try (var cluster = new Cluster()) {
      long start = nextStart();
      cluster.submit(LEGACY, cluster.newId(), definition(start, 12 * HOUR, 40));
      cluster.submit(EXACT, cluster.newId(), definition(start, 12 * HOUR, 40));
      long arrival = start + 12 * HOUR;
      for (int i = -warmup; i < measured; i++) {
        var legacy = new Attempt(LEGACY, cluster.newId(), arrival);
        var exact = new Attempt(EXACT, cluster.newId(), arrival);
        for (Attempt attempt : ordered(legacy, exact, order.nextBoolean())) {
          long before = System.nanoTime();
          cluster.client.submitReservation(attempt.submission);
          long elapsed = System.nanoTime() - before;
          if (i >= 0) rows.add(attempt.queue + ",submit," + i + "," + elapsed);
        }
        for (Attempt attempt : ordered(legacy, exact, order.nextBoolean())) {
          long before = System.nanoTime();
          cluster.client.updateReservation(attempt.update);
          long elapsed = System.nanoTime() - before;
          if (i >= 0) rows.add(attempt.queue + ",update," + i + "," + elapsed);
        }
        for (Attempt attempt : List.of(legacy, exact)) {
          assertEquals(6, cluster.plan(attempt.queue).getReservationById(attempt.id)
              .getResourcesAtTime(arrival).getVirtualCores());
          cluster.delete(attempt.id);
        }
      }
    }
    Files.write(Path.of(output), rows);
  }

  private record Attempt(String queue, ReservationId id, ReservationSubmissionRequest submission,
      ReservationUpdateRequest update) {
    Attempt(String queue, ReservationId id, long arrival) {
      this(queue, id, ReservationSubmissionRequest.newInstance(definition(arrival, DAY, 5), queue, id),
          ReservationUpdateRequest.newInstance(definition(arrival, DAY, 6), id));
    }
  }

  private static List<Attempt> ordered(Attempt legacy, Attempt exact, boolean exactFirst) {
    return exactFirst ? List.of(exact, legacy) : List.of(legacy, exact);
  }

  private static long nextStart() {
    return (System.currentTimeMillis() / MINUTE + 10) * MINUTE;
  }

  private static ReservationDefinition definition(long start, long duration, int containers) {
    var request = ReservationRequest.newInstance(
        Resource.newInstance(1024, 1), containers, containers, duration);
    var requests = ReservationRequests.newInstance(List.of(request), ReservationRequestInterpreter.R_ALL);
    return ReservationDefinition.newInstance(start, start + duration, requests, "quota-check");
  }

  private static void assertQuotaRejected(org.junit.jupiter.api.function.Executable call) {
    var failure = assertThrows(YarnException.class, call);
    assertTrue(failure.toString().contains(ExactCapacityOverTimePolicy.INTEGRAL_MESSAGE),
        failure::toString);
  }

  private static final class Cluster implements AutoCloseable {
    private final ResourceManager rm;
    private ApplicationClientProtocol client;

    Cluster() throws Exception {
      var conf = new CapacitySchedulerConfiguration();
      conf.setQueues(new QueuePath("root"), new String[]{LEGACY, EXACT});
      configureQueue(conf, LEGACY, CapacityOverTimePolicy.class);
      configureQueue(conf, EXACT, ExactCapacityOverTimePolicy.class);
      conf.setClass(YarnConfiguration.RM_SCHEDULER, CapacityScheduler.class, ResourceScheduler.class);
      conf.setBoolean(YarnConfiguration.RM_RESERVATION_SYSTEM_ENABLE, true);
      conf.set(YarnConfiguration.RM_ADDRESS, "127.0.0.1:0");
      conf.set(YarnConfiguration.RM_SCHEDULER_ADDRESS, "127.0.0.1:0");
      conf.set(YarnConfiguration.RM_ADMIN_ADDRESS, "127.0.0.1:0");
      conf.set(YarnConfiguration.RM_RESOURCE_TRACKER_ADDRESS, "127.0.0.1:0");
      conf.setBoolean(YarnConfiguration.RECOVERY_ENABLED, false);
      conf.setClass(YarnConfiguration.FS_NODE_ATTRIBUTE_STORE_IMPL_CLASS,
          NullNodeAttributeStore.class, NodeAttributeStore.class);
      rm = new ResourceManager() {
        @Override
        protected void startWepApp() {
          // This fixture exercises the client RPC service.
        }
      };
      try {
        rm.init(conf);
        rm.start();
        new MockNM("127.0.0.1:1", 204_800, 200, rm.getResourceTrackerService()).registerNode();
        var reservationSystem = rm.getRMContext().getReservationSystem();
        var capacity = Resource.newInstance(102_400, 100);
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
        do {
          reservationSystem.synchronizePlan(LEGACY, false);
          reservationSystem.synchronizePlan(EXACT, false);
          if (capacity.equals(plan(LEGACY).getTotalCapacity())
              && capacity.equals(plan(EXACT).getTotalCapacity())) break;
          Thread.sleep(20);
        } while (System.nanoTime() < deadline);
        assertEquals(capacity, plan(LEGACY).getTotalCapacity());
        assertEquals(capacity, plan(EXACT).getTotalCapacity());
        var address = rm.getClientRMService().getBindAddress();
        assertTrue(address.getPort() > 0);
        conf.set(YarnConfiguration.RM_ADDRESS, "127.0.0.1:" + address.getPort());
        client = ClientRMProxy.createRMProxy(conf, ApplicationClientProtocol.class);
      } catch (Exception | AssertionError failure) {
        close();
        throw failure;
      }
    }

    private static void configureQueue(CapacitySchedulerConfiguration conf, String queue,
        Class<? extends SharingPolicy> policy) {
      var path = new QueuePath("root." + queue);
      conf.setCapacity(path, 50);
      conf.setReservable(path, true);
      conf.setReservationWindow(path, DAY);
      conf.setAverageCapacity(path, 25);
      conf.setInstantaneousMaxCapacity(path, 100);
      conf.setReservationAdmissionPolicy(path, policy.getName());
    }

    Plan plan(String queue) {
      return rm.getRMContext().getReservationSystem().getPlan(queue);
    }

    ReservationId newId() throws Exception {
      return client.getNewReservation(GetNewReservationRequest.newInstance()).getReservationId();
    }

    void submit(String queue, ReservationId id, ReservationDefinition definition) throws Exception {
      client.submitReservation(ReservationSubmissionRequest.newInstance(definition, queue, id));
    }

    void update(ReservationId id, ReservationDefinition definition) throws Exception {
      client.updateReservation(ReservationUpdateRequest.newInstance(definition, id));
    }

    void delete(ReservationId id) throws Exception {
      client.deleteReservation(ReservationDeleteRequest.newInstance(id));
    }

    @Override
    public void close() {
      try {
        if (client != null) RPC.stopProxy(client);
      } finally {
        rm.stop();
      }
    }
  }
}
