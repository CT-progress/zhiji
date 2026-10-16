package dev.zhiji.control.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/**
 * Rate limiting configuration.
 *
 * <p>Registers {@link RateLimitProperties} and conditionally wires
 * {@link dev.zhiji.control.service.InMemoryRateLimiter} (default, single-machine)
 * or {@link dev.zhiji.control.service.RedisRateLimiter} (when
 * {@code zhiji.ratelimit.use-redis=true}, distributed).
 */
@Configuration
@EnableConfigurationProperties(RateLimitProperties.class)
public class RateLimitConfig {
}
