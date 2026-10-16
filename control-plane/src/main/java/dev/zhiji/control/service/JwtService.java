package dev.zhiji.control.service;

import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.JWSSigner;
import com.nimbusds.jose.crypto.MACSigner;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import dev.zhiji.control.config.JwtProperties;
import dev.zhiji.control.dto.TokenResponse;
import dev.zhiji.control.model.Role;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.Date;
import java.util.List;
import java.util.UUID;

/**
 * JWT issuance, verification, and refresh.
 *
 * <p>Issues HS256-signed JWTs with standard claims (sub, iss, iat, exp, jti)
 * plus application claims (roles, username, type). Access tokens have short
 * TTL; refresh tokens have long TTL and can be revoked.
 */
@Service
public class JwtService {

    private static final Logger log = LoggerFactory.getLogger(JwtService.class);

    private final JwtProperties props;
    private final JWSSigner signer;
    private final TokenRevocationService revocationService;

    public JwtService(JwtProperties props, TokenRevocationService revocationService) {
        this.props = props;
        this.revocationService = revocationService;
        try {
            this.signer = new MACSigner(props.secret());
        } catch (Exception e) {
            throw new IllegalStateException("Failed to create JWT signer", e);
        }
    }

    /**
     * Issue an access + refresh token pair for an authenticated user.
     */
    public TokenResponse issueTokens(String userId, String username, List<Role> roles) {
        String accessJti = UUID.randomUUID().toString();
        String refreshJti = UUID.randomUUID().toString();
        Instant now = Instant.now();
        Instant accessExpiry = now.plus(props.accessTokenTtl());
        Instant refreshExpiry = now.plus(props.refreshTokenTtl());

        List<String> roleStrings = roles.stream().map(Enum::name).toList();

        String accessToken = buildAndSign(
            userId, username, roleStrings, "access", accessJti, now, accessExpiry
        );
        String refreshToken = buildAndSign(
            userId, username, roleStrings, "refresh", refreshJti, now, refreshExpiry
        );

        return new TokenResponse(
            accessToken,
            refreshToken,
            props.accessTokenTtl().getSeconds(),
            "Bearer",
            username,
            roleStrings
        );
    }

    /**
     * Refresh tokens: validate the refresh token, revoke it (one-time use),
     * and issue a new pair.
     */
    public TokenResponse refresh(String refreshToken, String userId, String username, List<Role> roles) {
        // One-time use: revoke the old refresh token
        try {
            SignedJWT jwt = SignedJWT.parse(refreshToken);
            String jti = jwt.getJWTClaimsSet().getJWTID();
            var exp = jwt.getJWTClaimsSet().getExpirationTime();
            if (jti != null && exp != null) {
                revocationService.revoke(jti, exp.toInstant());
            }
        } catch (Exception e) {
            log.warn("failed to parse refresh token for revocation: {}", e.getMessage());
        }
        return issueTokens(userId, username, roles);
    }

    /**
     * Revoke a token by its jti (used for logout).
     */
    public void revoke(String jti, Instant expiresAt) {
        revocationService.revoke(jti, expiresAt);
    }

    private String buildAndSign(String userId, String username, List<String> roles,
                                 String tokenType, String jti,
                                 Instant issuedAt, Instant expiry) {
        try {
            JWTClaimsSet.Builder builder = new JWTClaimsSet.Builder()
                .subject(userId)
                .issuer(props.issuer())
                .issueTime(Date.from(issuedAt))
                .expirationTime(Date.from(expiry))
                .jwtID(jti)
                .claim("username", username)
                .claim("roles", roles)
                .claim("type", tokenType);

            SignedJWT jwt = new SignedJWT(
                new JWSHeader.Builder(JWSAlgorithm.HS256).build(),
                builder.build()
            );
            jwt.sign(signer);
            return jwt.serialize();
        } catch (Exception e) {
            throw new IllegalStateException("Failed to sign JWT", e);
        }
    }
}
