package dev.zhiji.control.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.Map;

/**
 * Rate limiting configuration: per-platform QPS limits.
 *
 * <p>Each platform (douyin, bilibili, zhihu, xiaohongshu) has an independent
 * token bucket. The default QPS applies when a platform is not explicitly
 * configured. When {@code use-redis} is true, a Redis-backed rate limiter
 * is used; otherwise an in-memory implementation is used (single-machine).
 */
@ConfigurationProperties(prefix = "zhiji.ratelimit")
public record RateLimitProperties(
    int defaultQps,
    Map<String, Integer> platformLimits,
    boolean useRedis
) {
    /**
     * Get the QPS limit for a given platform.
     */
    public int qpsFor(String platform) {
        if (platform == null) {
            return defaultQps();
        }
        return platformLimits().getOrDefault(platform, defaultQps());
    }
}
