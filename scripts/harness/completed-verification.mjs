/** 完成状态保留 AGENTS.md 允许的待验证项，实际失败与缺少通过证据仍阻止完成。 */
export function hasCompletedVerificationEvidence(verification) {
    if (!Array.isArray(verification)) return false;
    return (
        verification.some((item) => item?.status === "passed") &&
        verification.every(
            (item) =>
                item?.status === "passed" ||
                (item?.status === "not-run" && typeof item.summary === "string" && item.summary.trim().length > 0),
        )
    );
}
