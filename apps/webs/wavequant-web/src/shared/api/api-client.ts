/** Same-origin API access for the static build and development proxy. */
export async function readApi(path: string, signal?: AbortSignal): Promise<unknown> {
    const timeout = AbortSignal.timeout(30000);
    const response = await fetch(path, { signal: signal ? AbortSignal.any([signal, timeout]) : timeout });
    if (!response.ok) {
        throw new Error(response.status === 400 ? "日期或查询参数无效" : "数据暂时不可用，请稍后重试");
    }
    return response.json() as Promise<unknown>;
}
