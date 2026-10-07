import assert from "node:assert/strict";
import test from "node:test";

import { LectureOverlay } from "../public/lecture-overlay.js";

for (const level of [2, 3]) {
    for (const confirmed of [false, true]) {
        test(`level ${level} draws ${confirmed ? "confirmed direction solid with a live endpoint" : "unconfirmed direction dashed"}`, () => {
            const overlay = new LectureOverlay({ dataset: {} });
            const segments = [];
            let dash;
            const ctx = {
                save() {},
                restore() {},
                setLineDash(value) {
                    dash = value;
                },
                beginPath() {},
                moveTo() {},
                lineTo() {},
                stroke() {
                    segments.push({ dash: [...dash], color: this.strokeStyle });
                },
                fillText() {},
            };
            const points = [
                { index: 0, time: "2022-08-02", kind: "H", value: 10.66, label: "H2", available_at: "2024-08-15" },
                { index: 1, time: "2024-07-25", kind: "L", value: 3.45, label: "L8", available_at: "2025-03-11" },
                {
                    index: 2,
                    time: "2025-05-15",
                    kind: "H",
                    value: 19.63,
                    label: "当前高点",
                    available_at: "2025-05-15",
                    state: "developing",
                },
            ];
            const stroke = {
                id: `level${level}-live`,
                kind: level === 2 ? "secondary-developing" : "tertiary-developing",
                state: confirmed ? "confirmed" : "developing",
                wave_direction: "up",
                points,
                confirmation: confirmed
                    ? {
                          direction: "up",
                          available_at: "2025-03-11",
                          broken_key: points[0],
                          confirmed_by: { time: "2025-03-11", value: 11.44 },
                      }
                    : undefined,
            };
            overlay.strokes = [stroke];
            overlay.projected = [
                { stroke, points: points.map((point, index) => ({ point, index, x: index * 100, y: point.value })) },
            ];
            overlay.draw({ useMediaCoordinateSpace: (fn) => fn({ context: ctx }) });
            assert.deepEqual(
                segments.map((segment) => segment.dash),
                confirmed
                    ? [[], []]
                    : [
                          [7, 4],
                          [7, 4],
                      ],
            );
            assert.ok(segments.every((segment) => segment.color === (level === 2 ? "#d6a3ff" : "#ffad72")));
            const annotation = overlay.annotation(`drawing:${stroke.id}:2`);
            if (confirmed) {
                assert.equal(annotation.time, "2025-03-11");
                assert.match(annotation.title, /上涨趋势已确认/);
                assert.match(annotation.description, /2025-03-11 最高价 11.44 严格突破前高 2022-08-02 10.66/);
                assert.match(annotation.description, /2025-05-15 19.63.*继续延伸.*尚未固定/);
                assert.equal(annotation.raw.stroke.points.at(-1).state, "developing");
                stroke.confirmed_endpoint = points.at(-1);
                const pending = {
                    index: 3,
                    time: "2025-05-20",
                    kind: "L",
                    value: 16,
                    label: "回档低点",
                    available_at: "2025-05-22",
                    edge_state: "developing",
                };
                points.push(pending);
                overlay.projected[0].points.push({ point: pending, index: 3, x: 300, y: 16 });
                segments.length = 0;
                overlay.draw({ useMediaCoordinateSpace: (fn) => fn({ context: ctx }) });
                assert.deepEqual(
                    segments.map((segment) => segment.dash),
                    [[], [], [7, 4]],
                );
                assert.match(overlay.annotation(`drawing:${stroke.id}:3`).description, /当前端点 2025-05-15 19.63/);
            } else {
                assert.match(annotation.title, /完整发展路径/);
            }
        });
    }
}
