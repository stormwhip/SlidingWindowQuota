package org.apache.hadoop.yarn.server.resourcemanager.reservation;

import java.io.BufferedReader;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.SplittableRandom;
import java.util.TreeMap;

import org.junit.jupiter.api.Test;

import static org.apache.hadoop.yarn.server.resourcemanager.reservation.QuotaChanges.*;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class JavaQuotaReplayTest {
  private static final int WINDOW = 32;
  private static final int WARMUPS = 10;
  private static final int REPEATS = 15;

  record Request(Reservation old, Reservation next, boolean accepted, Transaction change) {}
  record Stream(String span, int rejection, List<Request> requests) {}
  record Workload(int seed, int slots, long quota, int end, long[] load,
      TreeMap<Long, Long> jumps, List<Stream> streams) {}

  @Test
  void replay() throws IOException {
    var input = Path.of(System.getProperty("quota.javaInput", "../results/native-replay/input.txt"));
    List<Workload> workloads = read(input);
    int cases = 0;
    for (var workload : workloads) {
      for (var stream : workload.streams) {
        verify(workload, stream);
        cases += stream.requests.size();
      }
    }
    System.out.println("Dense oracle, exact event scan and bridge tree agreed on " + cases + " requests");
    String output = System.getProperty("quota.javaSamples");
    if (output != null) {
      var rows = new ArrayList<String>();
      rows.add("seed,background_slots,span,target_rejection_pct,repeat,request,accepted,"
          + "capacity_query,scan_query_ns,scan_update_ns,hull_query_ns,hull_update_ns");
      for (var workload : workloads) {
        for (var stream : workload.streams) measure(workload, stream, rows);
      }
      Files.write(Path.of(output), rows);
    }
  }

  static List<Workload> read(Path path) throws IOException {
    var result = new ArrayList<Workload>();
    try (BufferedReader input = Files.newBufferedReader(path)) {
      int count = Integer.parseInt(input.readLine().trim());
      for (int i = 0; i < count; i++) {
        String[] header = fields(input);
        int seed = Integer.parseInt(header[0]);
        int slots = Integer.parseInt(header[1]);
        long quota = Long.parseLong(header[2]);
        int streamCount = Integer.parseInt(header[3]);
        int end = 4 * slots + WINDOW;
        var jumps = new TreeMap<Long, Long>();
        var load = new long[end];
        var background = fields(input);
        assertEquals(slots, background.length);
        for (int j = 0; j < slots; j++) {
          var r = new Reservation(4L * j, 4L * (j + 1), Long.parseLong(background[j]));
          adjustLoad(load, r, 1);
          addReservation(jumps, r, WINDOW);
        }
        // Pin the input grid for the bridge tree, as in the native replay.
        for (long x = 0; x <= end; x += 4) jumps.putIfAbsent(x, 0L);
        var streams = new ArrayList<Stream>();
        for (int s = 0; s < streamCount; s++) {
          String[] streamHeader = fields(input);
          var requests = new ArrayList<Request>();
          int length = Integer.parseInt(streamHeader[2]);
          for (int q = 0; q < length; q++) {
            String[] row = fields(input);
            var old = reservation(row, 0);
            var next = reservation(row, 3);
            requests.add(new Request(old, next, row[6].equals("1"), between(old, next, WINDOW, end)));
          }
          streams.add(new Stream(streamHeader[0], Integer.parseInt(streamHeader[1]), requests));
        }
        result.add(new Workload(seed, slots, quota, end, load, jumps, streams));
      }
      assertEquals(null, input.readLine(), "Unexpected trailing input");
    }
    return result;
  }

  private static String[] fields(BufferedReader input) throws IOException {
    return input.readLine().trim().split("\\s+");
  }

  private static Reservation reservation(String[] row, int offset) {
    return new Reservation(Long.parseLong(row[offset]), Long.parseLong(row[offset + 1]),
        Long.parseLong(row[offset + 2]));
  }

  private static void adjustLoad(long[] load, Reservation r, int sign) {
    long difference = sign * r.demand();
    for (int t = (int) r.start(); t < r.end(); t++) load[t] += difference;
  }

  private static Peak densePeak(long[] load, int first, int last) {
    long sum = 0;
    for (int x = Math.max(0, first - WINDOW); x < first; x++) sum += load[x];
    long best = sum;
    int earliest = first;
    for (int x = first; x < last; x++) {
      sum += load[x] - (x < WINDOW ? 0 : load[x - WINDOW]);
      if (sum > best) {
        best = sum;
        earliest = x + 1;
      }
    }
    return new Peak(best, earliest);
  }

  private static void verify(Workload w, Stream stream) {
    long[] load = w.load.clone();
    var events = new EventQuotaIndex(w.jumps);
    var hull = new BridgeQuotaIndex(w.jumps, w.end);
    var domain = new Piece(0, w.end, 0, 0);
    var kept = densePeak(load, 0, w.end);
    assertTrue(kept.value() <= w.quota);
    assertEquals(kept, events.peak(domain));
    assertEquals(kept, hull.peak(domain));
    int i = 0;
    for (Request q : stream.requests) {
      String label = w.seed + "/" + w.slots + "/" + stream.span + "/" + stream.rejection + "/" + i++;
      adjustLoad(load, q.old, -1);
      adjustLoad(load, q.next, 1);
      var proposed = densePeak(load, 0, w.end);
      for (Piece piece : q.change.pieces()) {
        Peak expected = densePeak(load, (int) piece.lo(), (int) piece.hi());
        assertEquals(expected, events.peak(piece), "scan proposed " + label);
        assertEquals(expected, hull.peak(piece), "tree proposed " + label);
      }
      assertEquals(q.accepted, proposed.value() <= w.quota, "oracle " + label);
      assertEquals(q.accepted, events.allows(q.change, w.quota), "scan decision " + label);
      assertEquals(q.accepted, hull.allows(q.change, w.quota), "tree decision " + label);
      if (q.accepted) {
        events.commit(q.change);
        hull.commit(q.change);
        kept = proposed;
      } else {
        adjustLoad(load, q.next, -1);
        adjustLoad(load, q.old, 1);
      }
      assertEquals(kept, events.peak(domain), "scan state " + label);
      assertEquals(kept, hull.peak(domain), "tree state " + label);
    }
    long sum = 0;
    for (int x = 0; x <= w.end; x++) {
      if (x != 0) sum += load[x - 1] - (x <= WINDOW ? 0 : load[x - 1 - WINDOW]);
      assertEquals(sum, events.valueAt(x));
      assertEquals(sum, hull.valueAt(x));
    }
  }

  private static void measure(Workload w, Stream stream, List<String> rows) {
    var order = new SplittableRandom(705L + w.seed);
    int n = stream.requests.size();
    long[][] timings = new long[n][4];
    boolean[] decisions = new boolean[2];
    for (int repetition = -WARMUPS; repetition < REPEATS; repetition++) {
      var events = new EventQuotaIndex(w.jumps);
      var hull = new BridgeQuotaIndex(w.jumps, w.end);
      for (int i = 0; i < n; i++) {
        Request q = stream.requests.get(i);
        Transaction change = q.change;
        boolean check = q.next.demand() != 0;
        int first = order.nextInt(2);
        for (int step = 0; step < 2; step++) {
          int side = first ^ step;
          if (side == 0) {
            long begin = System.nanoTime();
            boolean accept = !check || events.allows(change, w.quota);
            long checked = System.nanoTime();
            if (accept) events.commit(change);
            long finished = System.nanoTime();
            timings[i][0] = checked - begin;
            timings[i][1] = finished - checked;
            decisions[0] = accept;
          } else {
            long begin = System.nanoTime();
            boolean accept = !check || hull.allows(change, w.quota);
            long checked = System.nanoTime();
            if (accept) hull.commit(change);
            long finished = System.nanoTime();
            timings[i][2] = checked - begin;
            timings[i][3] = finished - checked;
            decisions[1] = accept;
          }
        }
        assertEquals(q.accepted, decisions[0]);
        assertEquals(q.accepted, decisions[1]);
      }
      if (repetition < 0) continue;
      for (int i = 0; i < n; i++) {
        Request q = stream.requests.get(i);
        long[] t = timings[i];
        rows.add(w.seed + "," + w.slots + "," + stream.span + "," + stream.rejection + ","
            + repetition + "," + i + "," + q.accepted + "," + (q.next.demand() != 0) + ","
            + t[0] + "," + t[1] + "," + t[2] + "," + t[3]);
      }
    }
  }
}
