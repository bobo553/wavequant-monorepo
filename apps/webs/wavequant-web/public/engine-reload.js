/** Only a ready engine fingerprint change refreshes the page; settings and outages do not. */
export function createEngineReloadMonitor({ readVersion, onReload, onUnavailable = () => {}, intervalMs = 3000 }) {
    let baseline = null;
    let timer = null;
    let request = null;
    let stopped = false;
    let reloading = false;

    async function check() {
        if (stopped || reloading || request) return;
        const controller = new AbortController();
        request = controller;
        const timeout = setTimeout(() => controller.abort(), 5000);
        try {
            const response = await readVersion(controller.signal);
            if (stopped || controller.signal.aborted) return;
            const engine = response?.engine_version;
            if (typeof engine !== "string" || !engine || typeof response.version !== "string" || !response.version) return;
            if (baseline === null) baseline = engine;
            else if (engine !== baseline) {
                reloading = true;
                clearInterval(timer);
                timer = null;
                onReload(engine);
            }
        } catch (error) {
            // Keep the last known engine across disconnects so recovery can detect its replacement.
            if (!stopped) onUnavailable(error);
        } finally {
            clearTimeout(timeout);
            request = null;
        }
    }

    return {
        check,
        start() {
            if (timer || stopped || reloading) return;
            timer = setInterval(() => void check(), intervalMs);
            void check();
        },
        stop() {
            stopped = true;
            clearInterval(timer);
            timer = null;
            request?.abort();
        },
    };
}
