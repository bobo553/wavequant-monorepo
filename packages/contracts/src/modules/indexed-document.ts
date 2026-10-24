import { z } from "zod";

const NodeIndexSchema = z.number().int().nonnegative().max(999_999);
export const IndexedDocumentSchema = z.object({
    $format: z.literal("wavequant.indexed.v1"),
    root: NodeIndexSchema,
    nodes: z.array(z.union([
        z.tuple([z.literal(0), z.null()]),
        z.tuple([z.literal(1), z.boolean()]),
        z.tuple([z.literal(2), z.number().finite()]),
        z.tuple([z.literal(3), z.string()]),
        z.tuple([z.literal(4), z.array(NodeIndexSchema)]),
        z.tuple([z.literal(5), z.array(z.tuple([z.string(), NodeIndexSchema]))]),
    ])).max(1_000_000),
});
export type TIndexedDocument = z.infer<typeof IndexedDocumentSchema>;
