package dev.zhiji.control.service;

import dev.zhiji.control.config.RateLimitProperties;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.util.concurrent.ConcurrentHashMap;

/**
 * In-memory token bucket rate limiter.
 *
 * <p>Single-machine implementation using ConcurrentHashMap. Each platform
 * gets its own bucket with a capacity equal to its QPS and a refill rate
 * of QPS tokens per second. Lazy refill: tokens are topped up on each
 * request based on elapsed time since the last refill.
 *
 * <p>Active when {@code zhiji.ratelimit.use-redis} is false or missing.
 */
@Component
@ConditionalOnProperty(name = "zhiji.ratelimit.use-redis", havingValue = "false", matchIfMissing = true)
public class InMemoryRateLimiter implements RateLimiter {

    private record Bucket(double tokens, long lastRefillNanos) {
        Bucket refill(int qps, long nowNanos) {
            long elapsedNanos = nowNanos - lastRefillNanos;
            if (elapsedNanos <= 0) {
                return this;
            }
            double newTokens = tokens + (elapsedNanos / 1_000_000_000.0) * qps;
            return new Bucket(Math.min(newTokens, qps), nowNanos);
        }
    }

    private final RateLimitProperties props;
    private final ConcurrentHashMap<String, Bucket> buckets = new ConcurrentHashMap<>();

    public InMemoryRateLimiter(RateLimitProperties props) {
        this.props = props;
    }

    @Override
    public boolean tryAcquire(String platform) {
        int qps = props.qpsFor(platform);
        String key = platform != null ? platform : "default";
        long now = System.nanoTime();
        Bucket current = buckets.compute(key, (k, v) -> {
            if (v == null) {
                return new Bucket(qps, now);
            }
            return v.refill(qps, now);
        });
        if (current.tokens() >= 1.0) {
            buckets.compute(key, (k, v) ->
                v == null ? new Bucket(qps - 1, now) : new Bucket(v.tokens() - 1, v.lastRefillNanos()));
            return true;
        }
        return false;
    }

    @Override
    public int qpsFor(String platform) {
        return props.qpsFor(platform);
    }

    /**
     * Clear all buckets (for testing).
     */
    public void clear() {
        buckets.clear();
    }
}
