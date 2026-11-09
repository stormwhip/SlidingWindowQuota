#include <algorithm>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

using I = std::int64_t;
using Wide = __int128_t;
using Clock = std::chrono::steady_clock;
constexpr I window = 32;

struct Reservation { I lo, hi, demand; };
struct Point { I x, y; };
struct Limit { I num = 0, den = 0, time = 0; };
struct Piece { I lo, hi, a, b; };
struct Request { Reservation old, next; bool accepted; Limit expected; };

void consider(Limit& best, Limit next) {
    if (!next.den) return;
    const Wide cmp = Wide(next.num) * best.den - Wide(best.num) * next.den;
    if (!best.den || cmp < 0 || (cmp == 0 && next.time < best.time)) best = next;
}

bool equal(Limit a, Limit b) {
    if (!a.den || !b.den) return !a.den && !b.den;
    return a.time == b.time && Wide(a.num) * b.den == Wide(b.num) * a.den;
}

I overlap(I t, Reservation r) {
    return r.demand ? std::max<I>(0, std::min(t, r.hi) - std::max(t - window, r.lo)) : 0;
}

std::vector<I> keys(Reservation r, I hi) {
    std::vector<I> result;
    if (r.demand) {
        for (I x : {r.lo, r.hi, r.lo + window, r.hi + window})
            if (x > 0 && x < hi) result.push_back(x);
        std::sort(result.begin(), result.end());
        result.erase(std::unique(result.begin(), result.end()), result.end());
    }
    return result;
}

struct Segment { I lo, hi, weight_a, weight_b, remove_a, remove_b; };

std::vector<Segment> segments(Reservation old, Reservation next, I end) {
    auto cuts = keys(old, end);
    const auto added = keys(next, end);
    cuts.insert(cuts.end(), added.begin(), added.end());
    cuts.push_back(0);
    cuts.push_back(end);
    std::sort(cuts.begin(), cuts.end());
    cuts.erase(std::unique(cuts.begin(), cuts.end()), cuts.end());
    std::vector<Segment> result;
    for (std::size_t i = 1; i < cuts.size(); ++i) {
        const I l = cuts[i - 1], r = cuts[i];
        const I w = overlap(l, next), removed = overlap(l, old) * old.demand;
        const I a = (overlap(r, next) - w) / (r - l);
        const I p = (overlap(r, old) * old.demand - removed) / (r - l);
        result.push_back({l, r, a, w - a * l, p, removed - p * l});
    }
    return result;
}

template<class Visit>
void positive_segments(Reservation old, Reservation next, I end, Visit visit) {
    for (auto s : segments(old, next, end)) {
        const bool left_zero = s.weight_a * s.lo + s.weight_b == 0;
        const bool right_zero = s.weight_a * s.hi + s.weight_b == 0;
        if (left_zero && right_zero) continue;
        s.lo += left_zero;
        s.hi -= right_zero;
        if (s.lo <= s.hi) visit(s);
    }
}

template<class Type>
struct Allocation {
    static inline std::size_t live = 0, peak = 0;
    Allocation() { peak = std::max(peak, ++live); }
    ~Allocation() { --live; }
};

struct Event : Allocation<Event> {
    I x, delta, slope, moment;
    std::uint64_t priority;
    std::unique_ptr<Event> left, right;
    Event(I x, I d, std::uint64_t p) : x(x), delta(d), slope(d), moment(x*d), priority(p) {}
};
using E = std::unique_ptr<Event>;

void refresh(Event& n) {
    n.slope = n.delta + (n.left ? n.left->slope : 0) + (n.right ? n.right->slope : 0);
    n.moment = n.x*n.delta + (n.left ? n.left->moment : 0) + (n.right ? n.right->moment : 0);
}

E merge(E l, E r) {
    if (!l) return r;
    if (!r) return l;
    if (l->priority >= r->priority) {
        l->right = merge(std::move(l->right), std::move(r));
        refresh(*l);
        return l;
    }
    r->left = merge(std::move(l), std::move(r->left));
    refresh(*r);
    return r;
}

void change(E& n, I x, I d, std::mt19937_64& rng) {
    if (!d) return;
    if (!n) {
        n = std::make_unique<Event>(x, d, rng());
        return;
    }
    if (x == n->x) {
        n->delta += d;
        if (!n->delta) {
            n = merge(std::move(n->left), std::move(n->right));
            return;
        }
    } else if (x < n->x) {
        change(n->left, x, d, rng);
        if (n->left && n->left->priority > n->priority) {
            E top = std::move(n->left);
            n->left = std::move(top->right);
            refresh(*n);
            top->right = std::move(n);
            n = std::move(top);
        }
    } else {
        change(n->right, x, d, rng);
        if (n->right && n->right->priority > n->priority) {
            E top = std::move(n->right);
            n->right = std::move(top->left);
            refresh(*n);
            top->left = std::move(n);
            n = std::move(top);
        }
    }
    refresh(*n);
}

Point prefix(const Event* n, I t) {
    I slope = 0, moment = 0;
    while (n) {
        if (t < n->x) n = n->left.get();
        else {
            slope += n->delta + (n->left ? n->left->slope : 0);
            moment += n->x*n->delta + (n->left ? n->left->moment : 0);
            n = n->right.get();
        }
    }
    return {slope, moment};
}

template<class Visit>
void scan(const Event* n, I lo, I hi, Visit& visit) {
    if (!n) return;
    if (n->x > lo) scan(n->left.get(), lo, hi, visit);
    if (lo < n->x && n->x < hi) visit(n->x, n->delta);
    if (n->x < hi) scan(n->right.get(), lo, hi, visit);
}

template<class Visit>
bool scan_until(const Event* n, I lo, I hi, Visit& exceeds) {
    if (!n) return false;
    return (n->x > lo && scan_until(n->left.get(), lo, hi, exceeds))
        || (lo < n->x && n->x < hi && exceeds(n->x, n->delta))
        || (n->x < hi && scan_until(n->right.get(), lo, hi, exceeds));
}

Limit scan_limit(const Event* n, Reservation old, Reservation next, I quota, I end) {
    Limit best;
    positive_segments(old, next, end, [&](Segment s) {
        auto [slope, moment] = prefix(n, s.lo);
        I current = quota + moment + (s.remove_a - slope)*s.lo + s.remove_b;
        I weight = s.weight_a*s.lo + s.weight_b, prev = s.lo;
        consider(best, {current, weight, prev});
        slope = s.remove_a - slope;
        auto visit = [&](I t, I delta) {
            const I gap = t - prev;
            current += slope*gap;
            weight += s.weight_a*gap;
            consider(best, {current, weight, t});
            slope -= delta;
            prev = t;
        };
        scan(n, s.lo, s.hi, visit);
        if (s.hi > prev) visit(s.hi, 0);
    });
    return best;
}

std::vector<Piece> update_pieces(Reservation old, Reservation next, I end) {
    std::vector<Piece> changes;
    for (const auto& s : segments(old, next, end)) {
        const I a = next.demand*s.weight_a-s.remove_a, b = next.demand*s.weight_b-s.remove_b;
        if (a || b) changes.push_back({s.lo,s.hi,a,b});
    }
    return changes;
}

bool scan_allows(const Event* root, Reservation old, Reservation next, I quota, I end) {
    for (const auto& p : update_pieces(old, next, end)) {
        auto [slope,moment] = prefix(root,p.lo);
        I current = (slope+p.a)*p.lo-moment+p.b, prev = p.lo;
        if (current > quota) return false;
        const auto [end_slope,end_moment] = prefix(root,p.hi);
        if ((end_slope+p.a)*p.hi-end_moment+p.b > quota) return false;
        slope += p.a;
        auto exceeds = [&](I t, I delta) {
            current += slope*(t-prev);
            slope += delta;
            prev = t;
            return current > quota;
        };
        if (scan_until(root,p.lo,p.hi,exceeds)) return false;
    }
    return true;
}

void event_commit(E& root, Reservation old, Reservation next, std::mt19937_64& rng) {
    std::vector<Point> changes;
    for (auto r : {Reservation{old.lo, old.hi, -old.demand}, next}) {
        if (!r.demand) continue;
        changes.insert(changes.end(), {{r.lo, r.demand}, {r.hi, -r.demand},
                                       {r.lo + window, -r.demand}, {r.hi + window, r.demand}});
    }
    std::sort(changes.begin(), changes.end(), [](Point l, Point r) { return l.x < r.x; });
    for (std::size_t i = 0; i < changes.size();) {
        const I x = changes[i].x;
        I d = 0;
        do { d += changes[i++].y; } while (i < changes.size() && changes[i].x == x);
        change(root, x, d, rng);
    }
}

struct Hull : Allocation<Hull> {
    I lo, hi, value, a = 0, b = 0, dy = 0, dx = 1;
    Point anchor{0, 0};
    int height = 1, refs;
    std::unique_ptr<Hull> left, right;
    Hull(I x, I y, int refs) : lo(x), hi(x), value(y), refs(refs) {}
    bool leaf() const { return !left; }
};
using H = std::unique_ptr<Hull>;

struct Cursor {
    const Hull* n;
    I a, b;
    explicit Cursor(const Hull* n, I a = 0, I b = 0) : n(n), a(a+n->a), b(b+n->b) {}
    void descend(bool right) { *this = Cursor(right ? n->right.get() : n->left.get(), a, b); }
    Point point() const { return {n->lo, n->value + a*n->lo + b}; }
    Point anchor() const { return {n->anchor.x, n->anchor.y + a*n->anchor.x + b}; }
    I dy() const { return n->dy + a*n->dx; }
};

std::pair<Point, Point> find_bridge(const Hull* left, const Hull* right) {
    Cursor l(left), r(right);
    const I middle_twice = left->hi + right->lo;
    while (!l.n->leaf() || !r.n->leaf()) {
        if (l.n->leaf()) {
            const Point x = l.point(), y = r.anchor();
            const Wide sign = Wide(x.y-y.y)*r.n->dx - Wide(r.dy())*(x.x-y.x);
            if (!sign) return {x,y};
            r.descend(sign > 0);
        } else if (r.n->leaf()) {
            const Point x = l.anchor(), y = r.point();
            const Wide sign = Wide(y.y-x.y)*l.n->dx - Wide(l.dy())*(y.x-x.x);
            if (!sign) return {x,y};
            l.descend(sign < 0);
        } else {
            const Point x = l.anchor(), y = r.anchor();
            const I ldx = l.n->dx, rdx = r.n->dx;
            const Wide sl = Wide(l.dy())*rdx - Wide(r.dy())*ldx;
            const Wide hl = (Wide(2)*x.y*ldx + Wide(l.dy())*(middle_twice-2*x.x))*rdx;
            const Wide hr = (Wide(2)*y.y*rdx + Wide(r.dy())*(middle_twice-2*y.x))*ldx;
            if (!sl && hl == hr) return {x,y};
            if (sl >= 0) {
                if (hl >= hr) l.descend(true);
                else r.descend(false);
            } else if (hl >= hr) r.descend(true);
            else l.descend(false);
        }
    }
    return {l.point(), r.point()};
}

void shift(Hull& n, I a, I b) { n.a += a; n.b += b; }
void push(Hull& n) {
    if ((n.a || n.b) && !n.leaf()) {
        shift(*n.left, n.a, n.b);
        shift(*n.right, n.a, n.b);
        n.a = n.b = 0;
    }
}
void refresh(Hull& n) {
    n.lo = n.left->lo;
    n.hi = n.right->hi;
    n.height = 1 + std::max(n.left->height, n.right->height);
    auto [l, r] = find_bridge(n.left.get(), n.right.get());
    n.anchor = l;
    n.dy = r.y-l.y;
    n.dx = r.x-l.x;
}

H combine(H l, H r) {
    H n = std::make_unique<Hull>(0, 0, 0);
    n->left = std::move(l);
    n->right = std::move(r);
    refresh(*n);
    return n;
}

void rotate_left(H& n) {
    H top = std::move(n->right);
    push(*top);
    n->right = std::move(top->left);
    refresh(*n);
    top->left = std::move(n);
    refresh(*top);
    n = std::move(top);
}
void rotate_right(H& n) {
    H top = std::move(n->left);
    push(*top);
    n->left = std::move(top->right);
    refresh(*n);
    top->right = std::move(n);
    refresh(*top);
    n = std::move(top);
}
void balance(H& n) {
    const int d = n->left->height - n->right->height;
    if (d > 1) {
        Hull& l = *n->left;
        push(l);
        if (l.left->height < l.right->height) rotate_left(n->left);
        rotate_right(n);
    } else if (d < -1) {
        Hull& r = *n->right;
        push(r);
        if (r.left->height > r.right->height) rotate_right(n->right);
        rotate_left(n);
    } else refresh(*n);
}

Hull* find(Hull* n, I x) {
    while (!n->leaf()) n = x <= n->left->hi ? n->left.get() : n->right.get();
    return n->lo == x ? n : nullptr;
}
void insert(H& n, I x, I y) {
    if (n->leaf()) {
        H item = std::make_unique<Hull>(x, y, 1);
        n = x < n->lo ? combine(std::move(item), std::move(n)) : combine(std::move(n), std::move(item));
        return;
    }
    push(*n);
    insert(x <= n->left->hi ? n->left : n->right, x, y);
    balance(n);
}
void erase(H& n, I x) {
    if (n->leaf()) {
        if (n->lo != x) throw std::logic_error("missing hull point");
        n.reset();
        return;
    }
    push(*n);
    erase(x <= n->left->hi ? n->left : n->right, x);
    if (!n->left || !n->right) {
        H survivor = n->left ? std::move(n->left) : std::move(n->right);
        n = std::move(survivor);
    } else balance(n);
}

I value(Cursor node, I x) {
    while (!node.n->leaf()) {
        const I l = node.n->left->hi, r = node.n->right->lo;
        if (x <= l) node.descend(false);
        else if (x >= r) node.descend(true);
        else {
            const I yl = value(Cursor(node.n->left.get(), node.a, node.b), l);
            const I yr = value(Cursor(node.n->right.get(), node.a, node.b), r);
            const Wide product = Wide(yl)*(r-x) + Wide(yr)*(x-l);
            if (product % (r-l)) throw std::logic_error("noninteger interpolation");
            return static_cast<I>(product / (r-l));
        }
    }
    return node.point().y;
}

bool apply_pieces(Hull& n, const std::vector<Piece>& pieces) {
    const Piece* only = nullptr;
    bool multiple = false;
    for (const auto& p : pieces) {
        if (p.lo <= n.hi && n.lo < p.hi) {
            multiple = only != nullptr;
            only = &p;
            if (multiple) break;
        }
    }
    if (!only) return false;
    if (!multiple && only->lo <= n.lo && n.hi < only->hi) {
        shift(n, only->a, only->b);
        return true;
    }
    push(n);
    const bool l = apply_pieces(*n.left, pieces);
    const bool r = apply_pieces(*n.right, pieces);
    if (l || r) refresh(n);
    return l || r;
}

void hull_commit(H& root, Reservation old, Reservation next) {
    const I end = root->hi;
    const auto before = keys(old, end), after = keys(next, end);
    for (I x : after) if (!std::binary_search(before.begin(), before.end(), x)) {
        if (Hull* existing = find(root.get(), x)) ++existing->refs;
        else insert(root, x, value(Cursor(root.get()), x));
    }
    apply_pieces(*root, update_pieces(old, next, end));
    for (I x : before) if (!std::binary_search(after.begin(), after.end(), x)) {
        Hull* existing = find(root.get(), x);
        if (!existing) throw std::logic_error("missing reference");
        if (--existing->refs == 0) erase(root, x);
    }
}

Limit subtree_limit(Cursor node, Segment s, I quota) {
    node.a -= s.remove_a;
    node.b -= s.remove_b;
    while (!node.n->leaf()) {
        if (!s.weight_a) node.descend(node.dy() > 0);
        else {
            I num = -node.dy(), den = s.weight_a*node.n->dx;
            if (den < 0) { num = -num; den = -den; }
            const Point p = node.anchor();
            const Wide sign = Wide(quota-p.y)*den - Wide(num)*(s.weight_a*p.x+s.weight_b);
            node.descend(sign != 0 && (s.weight_a > 0 ? sign > 0 : sign < 0));
        }
    }
    const Point p = node.point();
    return {quota-p.y, s.weight_a*p.x+s.weight_b, p.x};
}

void range_limit(Cursor node, I lo, I hi, Segment s, I quota, Limit& best) {
    if (lo > hi || node.n->hi < lo || hi < node.n->lo) return;
    if (lo <= node.n->lo && node.n->hi <= hi) {
        consider(best, subtree_limit(node, s, quota));
        return;
    }
    range_limit(Cursor(node.n->left.get(), node.a, node.b), lo, hi, s, quota, best);
    range_limit(Cursor(node.n->right.get(), node.a, node.b), lo, hi, s, quota, best);
}

Limit hull_limit(const Hull* root, Reservation old, Reservation next, I quota) {
    Limit best;
    positive_segments(old, next, root->hi, [&](Segment s) {
        for (I x : {s.lo, s.hi}) {
            consider(best, {quota-value(Cursor(root), x)+s.remove_a*x+s.remove_b,
                            s.weight_a*x+s.weight_b, x});
            if (s.lo == s.hi) break;
        }
        range_limit(Cursor(root), s.lo+1, s.hi-1, s, quota, best);
    });
    return best;
}

bool hull_exceeds(Cursor node, Piece p, I quota) {
    if (p.lo > p.hi || p.hi < node.n->lo || node.n->hi < p.lo) return false;
    if (p.lo <= node.n->lo && node.n->hi <= p.hi) {
        node.a += p.a;
        node.b += p.b;
        while (!node.n->leaf()) node.descend(node.dy() > 0);
        return node.point().y > quota;
    }
    return hull_exceeds(Cursor(node.n->left.get(),node.a,node.b),p,quota)
        || hull_exceeds(Cursor(node.n->right.get(),node.a,node.b),p,quota);
}

bool hull_allows(const Hull* root, Reservation old, Reservation next, I quota) {
    for (auto p : update_pieces(old, next, root->hi)) {
        if (value(Cursor(root),p.lo)+p.a*p.lo+p.b > quota ||
            value(Cursor(root),p.hi)+p.a*p.hi+p.b > quota) return false;
        ++p.lo;
        --p.hi;
        if (hull_exceeds(Cursor(root),p,quota)) return false;
    }
    return true;
}

struct Initial { I x, jump, load; int refs; };

std::vector<Initial> initial_points(const std::vector<I>& demand) {
    const auto offset = static_cast<std::size_t>(window / 4);
    std::vector<Initial> points(demand.size() + offset + 1);
    for (std::size_t i = 0; i < demand.size(); ++i) {
        points[i].jump += demand[i];
        points[i+1].jump -= demand[i];
        points[i+offset].jump -= demand[i];
        points[i+offset+1].jump += demand[i];
        for (auto k : {i,i+1,i+offset,i+offset+1}) ++points[k].refs;
    }
    I slope = 0, moment = 0;
    for (std::size_t i = 0; i < points.size(); ++i) {
        auto& p = points[i];
        p.x = static_cast<I>(4*i);
        slope += p.jump;
        moment += p.jump*p.x;
        p.load = slope*p.x-moment;
    }
    return points;
}

E build_events(const std::vector<Initial>& points, std::mt19937_64& rng) {
    struct Entry { I x, d; std::uint64_t p; int left = -1, right = -1; };
    std::vector<Entry> nodes;
    std::vector<int> stack;
    nodes.reserve(points.size());
    stack.reserve(points.size());
    for (const auto& point : points) if (point.jump) {
        const int index = static_cast<int>(nodes.size());
        Entry next{point.x, point.jump, rng()};
        while (!stack.empty() && nodes[stack.back()].p < next.p) {
            next.left = stack.back();
            stack.pop_back();
        }
        if (!stack.empty()) nodes[stack.back()].right = index;
        nodes.push_back(next);
        stack.push_back(index);
    }
    auto build = [&](auto&& self, int index) -> E {
        if (index < 0) return nullptr;
        const auto& n = nodes[index];
        E result = std::make_unique<Event>(n.x, n.d, n.p);
        result->left = self(self, n.left);
        result->right = self(self, n.right);
        refresh(*result);
        return result;
    };
    return stack.empty() ? nullptr : build(build, stack.front());
}

H build_hull(const std::vector<Initial>& points, std::size_t start, std::size_t end) {
    if (end-start == 1) {
        const auto& p = points[start];
        return std::make_unique<Hull>(p.x, p.load, p.refs);
    }
    const auto mid = start+(end-start)/2;
    return combine(build_hull(points,start,mid), build_hull(points,mid,end));
}

struct Stream { std::string span; int rejection; std::vector<Request> requests; };
struct Case { int seed; I quota; std::vector<I> demand; std::vector<Stream> streams; };

std::vector<Case> read(std::istream& in) {
    int cases;
    if (!(in >> cases) || cases < 1) throw std::runtime_error("invalid replay");
    std::vector<Case> result(cases);
    for (auto& c : result) {
        int n, streams;
        if (!(in >> c.seed >> n >> c.quota >> streams) || n < 9 || n > 1000000 ||
            c.quota < 0 || c.quota > 1000000 || streams < 1)
            throw std::runtime_error("invalid case");
        c.demand.resize(n);
        for (I& d : c.demand) {
            if (!(in >> d) || d < 0 || d > 1000) throw std::runtime_error("invalid background");
        }
        c.streams.resize(streams);
        for (auto& stream : c.streams) {
            int q;
            if (!(in >> stream.span >> stream.rejection >> q) || q < 1)
                throw std::runtime_error("invalid stream");
            stream.requests.resize(q);
            for (auto& r : stream.requests) {
                if (!(in >> r.old.lo >> r.old.hi >> r.old.demand
                         >> r.next.lo >> r.next.hi >> r.next.demand >> r.accepted
                         >> r.expected.num >> r.expected.den >> r.expected.time))
                    throw std::runtime_error("truncated request");
                for (auto x : {r.old,r.next})
                    if (x.demand < 0 || x.demand > 1000000 || x.lo < 0 || x.hi > I(4)*n ||
                        (x.demand && x.lo >= x.hi)) throw std::runtime_error("invalid reservation");
            }
        }
    }
    return result;
}

I elapsed(Clock::time_point a, Clock::time_point b) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(b-a).count();
}
struct Timing { I query, update; };

void run(const Case& c, const Stream& stream, int repeats, std::ostream& samples,
         std::ostream& builds, bool decision_only) {
    const auto points = initial_points(c.demand);
    const I end = points.back().x;
    for (int rep = -2; rep < repeats; ++rep) {
        std::mt19937_64 priority(701), order(705+c.seed+rep);
        Event::peak = Hull::peak = 0;
        E events;
        H hull;
        I event_build = 0, hull_build = 0;
        for (int side = 0; side < 2; ++side) {
            const auto started = Clock::now();
            if ((side+rep)%2 != 0) {
                hull = build_hull(points,0,points.size());
                hull_build = elapsed(started, Clock::now());
            } else {
                events = build_events(points,priority);
                event_build = elapsed(started, Clock::now());
            }
        }
        std::vector<std::pair<Timing,Timing>> times;
        times.reserve(stream.requests.size());
        for (const auto& q : stream.requests) {
            Timing a{}, b{};
            for (int step = 0, first = static_cast<int>(order() & 1); step < 2; ++step) {
                const bool is_hull = (step ^ first) != 0;
                const auto start = Clock::now();
                Limit answer;
                bool accepted = true;
                if (q.next.demand) {
                    if (decision_only) {
                        accepted = is_hull ? hull_allows(hull.get(),q.old,q.next,c.quota)
                                           : scan_allows(events.get(),q.old,q.next,c.quota,end);
                    } else {
                        answer = is_hull ? hull_limit(hull.get(),q.old,q.next,c.quota)
                                         : scan_limit(events.get(),q.old,q.next,c.quota,end);
                        accepted = !answer.den || Wide(q.next.demand)*answer.den <= answer.num;
                    }
                }
                const auto checked = Clock::now();
                if (accepted) {
                    if (is_hull) hull_commit(hull,q.old,q.next);
                    else event_commit(events,q.old,q.next,priority);
                }
                const auto finished = Clock::now();
                if (accepted != q.accepted || (!decision_only && !equal(answer,q.expected))) {
                    throw std::runtime_error(std::string(is_hull ? "hull" : "event")
                        + " disagrees with exact replay at request " + std::to_string(times.size())
                        + ", seed " + std::to_string(c.seed) + ", span " + stream.span);
                }
                (is_hull ? b : a) = Timing{elapsed(start,checked),elapsed(checked,finished)};
            }
            times.emplace_back(a,b);
        }
        for (I t = 0; t <= end; t += std::max<I>(1,end/220)) {
            const auto [slope,moment] = prefix(events.get(),t);
            if (slope*t-moment != value(Cursor(hull.get()),t)) throw std::runtime_error("final states differ");
        }
        if (rep >= 0) {
            const auto write_key = [&](std::ostream& out) -> std::ostream& {
                return out << c.seed << ',' << c.demand.size() << ',' << stream.span << ','
                           << stream.rejection << ',' << rep << ',';
            };
            write_key(builds) << event_build << ',' << hull_build << ','
                << Event::peak*sizeof(Event) << ',' << Hull::peak*sizeof(Hull) << '\n';
            for (std::size_t i = 0; i < times.size(); ++i) {
                const auto& [a,b] = times[i];
                const auto& req = stream.requests[i];
                write_key(samples) << i << ',' << req.accepted << ',' << bool(req.next.demand) << ','
                    << a.query << ',' << a.update << ',' << b.query << ',' << b.update << '\n';
            }
        }
    }
}

int main(int argc, char** argv) {
    try {
        const bool decision_only = argc == 6 && std::string(argv[5]) == "--decision-only";
        if (argc != 5 && !decision_only)
            throw std::runtime_error("usage: native_replay input samples.csv builds.csv repeats [--decision-only]");
        std::ifstream source(argv[1]);
        const auto cases = read(source);
        std::ofstream samples(argv[2]), builds(argv[3]);
        if (!samples || !builds) throw std::runtime_error("could not create result files");
        samples << "seed,background_slots,span,target_rejection_pct,repeat,request,accepted,capacity_query,"
                   "scan_query_ns,scan_update_ns,hull_query_ns,hull_update_ns\n";
        builds << "seed,background_slots,span,target_rejection_pct,repeat,scan_build_ns,hull_build_ns,"
                  "scan_peak_node_bytes,hull_peak_node_bytes\n";
        const int repeats = std::stoi(argv[4]);
        if (repeats < 1) throw std::runtime_error("expected positive repeat count");
        for (const auto& c : cases)
            for (const auto& s : c.streams) run(c,s,repeats,samples,builds,decision_only);
        std::cout << "Event node: " << sizeof(Event) << " bytes; hull node: "
                  << sizeof(Hull) << " bytes\n";
    } catch (const std::exception& ex) {
        std::cerr << ex.what() << '\n';
        return 1;
    }
}
