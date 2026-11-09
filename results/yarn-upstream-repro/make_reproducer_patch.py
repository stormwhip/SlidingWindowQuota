"""Build the standalone Hadoop test patch from the pinned upstream test."""

from difflib import unified_diff
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REL = ("hadoop-yarn-project/hadoop-yarn/hadoop-yarn-server/"
       "hadoop-yarn-server-resourcemanager/src/test/java/org/apache/hadoop/yarn/"
       "server/resourcemanager/reservation/TestCapacityOverTimePolicy.java")
NAME = Path(REL).name
METHOD = """  @Test
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
"""

original = (ROOT / "base" / NAME).read_text(encoding="utf-8")
updated = original.replace("import java.util.Collection;\n", (
    "import java.util.Collection;\nimport java.util.Collections;\n"))
updated = updated.replace("import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningException;", (
    "import org.apache.hadoop.yarn.api.records.ReservationDefinition;\n"
    "import org.apache.hadoop.yarn.api.records.Resource;\n"
    "import org.apache.hadoop.yarn.server.resourcemanager.reservation.exceptions.PlanningException;"))
updated = updated.replace("import org.junit.jupiter.params.ParameterizedTest;", (
    "import org.apache.hadoop.yarn.server.resourcemanager.reservation.planning.ReservationAgent;\n"
    "import org.apache.hadoop.yarn.server.resourcemanager.scheduler.QueueMetrics;\n"
    "import org.apache.hadoop.yarn.util.resource.DefaultResourceCalculator;\n"
    "import org.apache.hadoop.yarn.util.resource.ResourceCalculator;\n"
    "import org.junit.jupiter.api.Test;\n"
    "import org.junit.jupiter.params.ParameterizedTest;"))
updated = updated.replace("import static org.mockito.Mockito.mock;\n", "")
imports = ("import static org.junit.jupiter.api.Assertions.assertThrows;\n"
           "import static org.junit.jupiter.api.Assertions.assertTrue;\n"
           "import static org.mockito.Mockito.mock;\n\n")
assert updated.count("import java.io.IOException;") == 1
updated = updated.replace("import java.io.IOException;", imports + "import java.io.IOException;")
closing = updated.rindex("\n}")
updated = updated[:closing] + "\n" + METHOD + updated[closing:]
lines = unified_diff(original.splitlines(keepends=True),
                     updated.splitlines(keepends=True), "a/" + REL, "b/" + REL)
patch = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                for line in lines)
(ROOT / "yarn-quota-missed-peak.patch").write_text(patch, encoding="utf-8", newline="\n")
(ROOT / "modified-test").mkdir(exist_ok=True)
(ROOT / "modified-test" / NAME).write_text(updated, encoding="utf-8", newline="\n")
