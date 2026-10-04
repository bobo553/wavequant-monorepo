/** Display precedence only: shorter observed connections win, with a stable endpoint/id tie break.
 * Separate incoming/outgoing slots guarantee at most one of each per group without changing source evidence.
 * Apply after the as-of filter and before viewport clipping; sorting costs O(E log E), slots cost O(E).
 */
export function selectWaveConnections(connections) {
    const candidates = connections.flatMap((connection) => {
        const [start, end] = connection.points || [];
        const duration = Date.parse(end?.time) - Date.parse(start?.time);
        if (
            connection.points?.length !== 2 ||
            !Number.isFinite(duration) ||
            duration <= 0 ||
            !Number.isFinite(start.value) ||
            !Number.isFinite(end.value)
        )
            return [];
        return [{ connection, duration }];
    });
    const compare = (left, right) => (left < right ? -1 : left > right ? 1 : 0);
    candidates.sort(
        (left, right) =>
            left.duration - right.duration ||
            compare(left.connection.group, right.connection.group) ||
            compare(right.connection.points[0].time, left.connection.points[0].time) ||
            compare(left.connection.points[1].time, right.connection.points[1].time) ||
            left.connection.points[0].value - right.connection.points[0].value ||
            left.connection.points[1].value - right.connection.points[1].value ||
            compare(left.connection.id || "", right.connection.id || ""),
    );
    const outgoing = new Set();
    const incoming = new Set();
    return candidates.flatMap(({ connection }) => {
        const key = (point) => JSON.stringify([connection.group, point.time, point.value]);
        const start = key(connection.points[0]);
        const end = key(connection.points[1]);
        if (outgoing.has(start) || incoming.has(end)) return [];
        outgoing.add(start);
        incoming.add(end);
        return [connection];
    });
}
