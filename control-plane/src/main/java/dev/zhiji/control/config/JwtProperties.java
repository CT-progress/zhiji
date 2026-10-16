package dev.zhiji.control.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

/**
 * JWT configuration for token issuance and verification.
 *
 * <p>Uses HS256 (symmetric key) for simplicity. In production with external
 * IAM, switch to RS256 with a key pair.
 */
@ConfigurationProperties(prefix = "zhiji.jwt")
public record JwtProperties(
    String secret,
    Duration accessTokenTtl,
    Duration refreshTokenTtl,
    String issuer
) {
    /**
     * Minimum secret length in bytes for HS256.
     */
    public static final int MIN_SECRET_BYTES = 32;
}
