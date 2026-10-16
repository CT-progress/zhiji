package dev.zhiji.control.service;

import dev.zhiji.control.config.RateLimitProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.time.Duration;

/**
 * Redis-backed token bucket rate limiter.
 *
 * <p>Uses a Lua-style INCR + EXPIRE approach for distributed rate counting.
 * Each platform gets a Redis key with a 1-second TTL; the counter resets
 * when the key expires. This gives a simple per-second QPS limit.
 *
 * <p>For a true token bucket (with burst capacity), a Lua script would
 * be needed. This simple counter approach is sufficient for the project's
 * scale.
 *
 * <p>Active when {@code zhiji.ratelimit.use-redis=true}.
 */
@Component
@ConditionalOnProperty(name = "zhiji.ratelimit.use-redis", havingValue = "true")
public class RedisRateLimiter implements RateLimiter {

    private static final Logger log = LoggerFactory.getLogger(RedisRateLimiter.class);

    private final StringRedisTemplate redis;
    private final RateLimitProperties props;

    public RedisRateLimiter(StringRedisTemplate redis, RateLimitProperties props) {
        this.redis = redis;
        this.props = props;
    }

    @Override
    public boolean tryAcquire(String platform) {
        int qps = props.qpsFor(platform);
        String key = "ratelimit:" + (platform != null ? platform : "default");
        try {
            Long count = redis.opsForValue().increment(key);
            if (count != null && count == 1) {
                redis.expire(key, Duration.ofSeconds(1));
            }
            return count != null && count <= qps;
        } catch (Exception e) {
            log.warn("Redis rate limit failed for {}: {}, falling back to allow", platform, e.getMessage());
            return true;
        }
    }

    @Override
    public int qpsFor(String platform) {
        return props.qpsFor(platform);
    }
}
