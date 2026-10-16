package dev.zhiji.control.service;

/**
 * Rate limiter interface for per-platform QPS control.
 *
 * <p>Implementations: in-memory token bucket (default) or Redis-backed
 * token bucket (distributed). The control plane calls {@link #tryAcquire}
 * before dispatching a task to the worker.
 */
public interface RateLimiter {

    /**
     * Attempt to acquire a token for the given platform.
     *
     * @param platform the platform key (douyin, bilibili, zhihu, xiaohongshu)
     * @return true if the request is allowed; false if rate limited
     */
    boolean tryAcquire(String platform);

    /**
     * Get the configured QPS for a platform.
     */
    int qpsFor(String platform);
}
