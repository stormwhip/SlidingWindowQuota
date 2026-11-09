/*******************************************************************************
 *   Licensed to the Apache Software Foundation (ASF) under one
 *   or more contributor license agreements.  See the NOTICE file
 *   distributed with this work for additional information
 *   regarding copyright ownership.  The ASF licenses this file
 *   to you under the Apache License, Version 2.0 (the
 *   "License"); you may not use this file except in compliance
 *   with the License.  You may obtain a copy of the License at
 *
 *       http://www.apache.org/licenses/LICENSE-2.0
 *
 *   Unless required by applicable law or agreed to in writing, software
 *   distributed under the License is distributed on an "AS IS" BASIS,
 *   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *   See the License for the specific language governing permissions and
 *   limitations under the License.
 *******************************************************************************/
package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;

import java.io.IOException;
import java.util.Arrays;
import java.util.Collection;
import java.util.Collections;

import net.jcip.annotations.NotThreadSafe;

import org.apache.hadoop.yarn.api.records.ReservationDefinition;
import org.apache.hadoop.yarn.api.records.Resource;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningException;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningQuotaException;
import org.apache.hadoop.yarn.server.resourcemanager.reservation.planning.ReservationAgent;
import org.apache.hadoop.yarn.server.resourcemanager.scheduler.QueueMetrics;
import org.apache.hadoop.yarn.util.resource.DefaultResourceCalculator;
import org.apache.hadoop.yarn.util.resource.ResourceCalculator;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;

/**
 * This class tests the {@code CapacityOvertimePolicy} sharing policy.
 */
@NotThreadSafe
@SuppressWarnings("VisibilityModifier")
public class TestCapacityOverTimePolicy extends BaseSharingPolicyTest {

  final static long ONEDAY = 86400 * 1000;
  final static long ONEHOUR = 3600 * 1000;
  final static long ONEMINUTE = 60 * 1000;
  final static String TWOHOURPERIOD = "7200000";
  final static String ONEDAYPERIOD = "86400000";

  public void initTestCapacityOverTimePolicy(long pDuration,
      double pHeight, int pNumSubmissions, String pRecurrenceExpression, Class pExpectedError) {
    this.duration = pDuration;
    this.height = pHeight;
    this.numSubmissions = pNumSubmissions;
    this.recurrenceExpression = pRecurrenceExpression;
    this.expectedError = pExpectedError;
    super.setup();
  }

  public static Collection<Object[]> data() {
    return Arrays.asList(new Object[][] {

        // easy fit
        {ONEHOUR, 0.25, 1, null, null },
        {ONEHOUR, 0.25, 1, TWOHOURPERIOD, null },
        {ONEHOUR, 0.25, 1, ONEDAYPERIOD, null },

        // instantaneous high, but fit integral and inst limits
        {ONEMINUTE, 0.74, 1, null, null },
        {ONEMINUTE, 0.74, 1, TWOHOURPERIOD, null },
        {ONEMINUTE, 0.74, 1, ONEDAYPERIOD, null },

        // barely fit
        {ONEHOUR, 0.76, 1, null, PlanningQuotaException.class },
        {ONEHOUR, 0.76, 1, TWOHOURPERIOD, PlanningQuotaException.class },
        {ONEHOUR, 0.76, 1, ONEDAYPERIOD, PlanningQuotaException.class },

        // overcommit with single reservation
        {ONEHOUR, 1.1, 1, null, PlanningQuotaException.class },
        {ONEHOUR, 1.1, 1, TWOHOURPERIOD, PlanningQuotaException.class },
        {ONEHOUR, 1.1, 1, ONEDAYPERIOD, PlanningQuotaException.class },

        // barely fit with multiple reservations (instantaneously, lowering to
        // 1min to fit integral)
        {ONEMINUTE, 0.25, 3, null, null },
        {ONEMINUTE, 0.25, 3, TWOHOURPERIOD, null },
        {ONEMINUTE, 0.25, 3, ONEDAYPERIOD, null },

        // overcommit with multiple reservations (instantaneously)
        {ONEMINUTE, 0.25, 4, null, PlanningQuotaException.class },
        {ONEMINUTE, 0.25, 4, TWOHOURPERIOD, PlanningQuotaException.class },
        {ONEMINUTE, 0.25, 4, ONEDAYPERIOD, PlanningQuotaException.class },

        // (non-periodic) reservation longer than window
        {25 * ONEHOUR, 0.25, 1, null, PlanningQuotaException.class },
        // NOTE: we generally don't accept periodicity < duration but the test
        // generator will "wrap" this correctly
        {25 * ONEHOUR, 0.25, 1, TWOHOURPERIOD, PlanningQuotaException.class },
        {25 * ONEHOUR, 0.25, 1, ONEDAYPERIOD, PlanningQuotaException.class },

        // (non-periodic) reservation longer than window
        {25 * ONEHOUR, 0.05, 5, null, PlanningQuotaException.class },
        // NOTE: we generally don't accept periodicity < duration but the test
        // generator will "wrap" this correctly
        {25 * ONEHOUR, 0.05, 5, TWOHOURPERIOD, PlanningQuotaException.class },
        {25 * ONEHOUR, 0.05, 5, ONEDAYPERIOD, PlanningQuotaException.class },

        // overcommit integral
        {ONEDAY, 0.26, 1, null, PlanningQuotaException.class },
        {2 * ONEHOUR, 0.26, 1, TWOHOURPERIOD, PlanningQuotaException.class },
        {2 * ONEDAY, 0.26, 1, ONEDAYPERIOD, PlanningQuotaException.class },

        // overcommit integral
        {ONEDAY / 2, 0.51, 1, null, PlanningQuotaException.class },
        {2 * ONEHOUR / 2, 0.51, 1, TWOHOURPERIOD,
            PlanningQuotaException.class },
        {2 * ONEDAY / 2, 0.51, 1, ONEDAYPERIOD, PlanningQuotaException.class }

    });
  }

  @Override
  public SharingPolicy getInitializedPolicy() {

    // 24h window
    long timeWindow = 86400000L;

    // 1 sec step
    long step = 1000L;

    // 25% avg cap on capacity
    float avgConstraint = 25;

    // 70% instantaneous cap on capacity
    float instConstraint = 75;

    String reservationQ =
        ReservationSystemTestUtil.getFullReservationQueueName();
    conf = ReservationSystemTestUtil.createConf(reservationQ, timeWindow,
        instConstraint, avgConstraint);
    CapacityOverTimePolicy policy = new CapacityOverTimePolicy();
    policy.init(reservationQ, conf);
    return policy;
  }

  @ParameterizedTest(name = "Duration {0}, height {1}," +
      " numSubmission {2}, periodic {3})")
  @MethodSource("data")
  public void testAllocation(long pDuration, double pHeight, int pNumSubmissions,
      String pRecurrenceExpression, Class pExpectedError)
      throws IOException, PlanningException {
    initTestCapacityOverTimePolicy(pDuration, pHeight, pNumSubmissions,
        pRecurrenceExpression, pExpectedError);
    runTest();
  }

  @Test
  public void testRejectsQuotaPeakBetweenIntegralSamples()
      throws PlanningException {
    String queue = ReservationSystemTestUtil.getFullReservationQueueName();
    Resource min = Resource.newInstance(1, 1);
    Resource total = Resource.newInstance(100, 100);
    ResourceCalculator calculator = new DefaultResourceCalculator();
    InMemoryPlan plan = new InMemoryPlan(mock(QueueMetrics.class),
        getInitializedPolicy(), mock(ReservationAgent.class), total, 1000L,
        calculator, min, total, queue, null, true,
        ReservationSystemTestUtil.createMockRMContext());

    long t0 = 1791808200000L;
    long halfway = t0 + 12 * ONEHOUR;
    long end = t0 + 36 * ONEHOUR;
    assertTrue(plan.addReservation(
        flatAllocation(t0, halfway, 40, queue, calculator, min), false));

    // The first 24h contain 12h at 40 MiB and 12h at 20 MiB.
    // Their 30 MiB average exceeds the configured 25 MiB quota.
    // The sampled right endpoints each see only 480 MiB-hours.
    assertThrows(PlanningQuotaException.class, () -> plan.addReservation(
        flatAllocation(halfway, end, 20, queue, calculator, min), false));
  }

  private static InMemoryReservationAllocation flatAllocation(long start,
      long end, int memory, String queue, ResourceCalculator calculator,
      Resource minimum) {
    ReservationDefinition definition = ReservationSystemTestUtil
        .createSimpleReservationDefinition(start, end, end - start, 1, null);
    return new InMemoryReservationAllocation(
        ReservationSystemTestUtil.getNewReservationId(), definition, "u1",
        queue, start, end,
        Collections.singletonMap(new ReservationInterval(start, end),
            Resource.newInstance(memory, 1)), calculator, minimum);
  }

}