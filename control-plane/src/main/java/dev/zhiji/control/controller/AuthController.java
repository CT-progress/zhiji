package dev.zhiji.control.controller;

import dev.zhiji.control.dto.*;
import dev.zhiji.control.model.User;
import dev.zhiji.control.service.AuthService;
import dev.zhiji.control.service.JwtService;
import dev.zhiji.control.service.TokenRevocationService;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;

/**
 * Authentication endpoints: register, login, refresh, logout, me.
 *
 * <p>Register and login are public (no auth required). Refresh validates
 * the refresh token's subject against the user store and checks the
 * revocation list (one-time use). Logout revokes the current token.
 * Me returns the authenticated user's profile.
 */
@RestController
@RequestMapping("/api/v1/auth")
public class AuthController {

    private static final Logger log = LoggerFactory.getLogger(AuthController.class);

    private final AuthService authService;
    private final JwtService jwtService;
    private final TokenRevocationService revocationService;

    public AuthController(AuthService authService, JwtService jwtService,
                          TokenRevocationService revocationService) {
        this.authService = authService;
        this.jwtService = jwtService;
        this.revocationService = revocationService;
    }

    @PostMapping("/register")
    public ResponseEntity<TokenResponse> register(@Valid @RequestBody RegisterRequest request) {
        try {
            User user = authService.register(request.username(), request.password(), request.displayName());
            TokenResponse tokens = jwtService.issueTokens(
                user.getId(), user.getUsername(),
                java.util.List.copyOf(user.getRoles())
            );
            return ResponseEntity.status(HttpStatus.CREATED).body(tokens);
        } catch (IllegalArgumentException e) {
            return ResponseEntity.badRequest().build();
        } catch (IllegalStateException e) {
            return ResponseEntity.status(HttpStatus.CONFLICT).build();
        }
    }

    @PostMapping("/login")
    public ResponseEntity<TokenResponse> login(@Valid @RequestBody LoginRequest request) {
        try {
            TokenResponse tokens = authService.login(request.username(), request.password());
            return ResponseEntity.ok(tokens);
        } catch (IllegalArgumentException e) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }
    }

    @PostMapping("/refresh")
    public ResponseEntity<TokenResponse> refresh(@Valid @RequestBody RefreshRequest request) {
        try {
            Jwt jwt = decodeJwt(request.refreshToken());
            if (jwt == null) {
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
            }
            String tokenType = jwt.getClaim("type");
            if (!"refresh".equals(tokenType)) {
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
            }
            // Check revocation (one-time use)
            String jti = jwt.getId();
            if (jti != null && revocationService.isRevoked(jti)) {
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
            }
            String userId = jwt.getSubject();
            TokenResponse tokens = authService.refresh(request.refreshToken(), userId);
            return ResponseEntity.ok(tokens);
        } catch (Exception e) {
            log.warn("refresh failed: {}", e.getMessage());
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }
    }

    @PostMapping("/logout")
    public ResponseEntity<Void> logout() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth != null && auth.getPrincipal() instanceof Jwt jwt) {
            String jti = jwt.getId();
            var exp = jwt.getExpiresAt();
            if (jti != null && exp != null) {
                jwtService.revoke(jti, exp);
            }
        }
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/me")
    public ResponseEntity<UserInfo> me() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth == null || !(auth.getPrincipal() instanceof Jwt jwt)) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }
        String userId = jwt.getSubject();
        User user = authService.getUser(userId);
        return ResponseEntity.ok(new UserInfo(
            user.getId(),
            user.getUsername(),
            user.getDisplayName(),
            user.getRoles().stream().map(Enum::name).toList(),
            user.getCreatedAt()
        ));
    }

    private Jwt decodeJwt(String token) {
        try {
            com.nimbusds.jwt.SignedJWT signed = com.nimbusds.jwt.SignedJWT.parse(token);
            var claims = signed.getJWTClaimsSet();
            return org.springframework.security.oauth2.jwt.Jwt
                .withTokenValue(token)
                .header("alg", "HS256")
                .subject(claims.getSubject())
                .issuer(claims.getIssuer())
                .issuedAt(claims.getIssueTime().toInstant())
                .expiresAt(claims.getExpirationTime().toInstant())
                .claim("username", claims.getStringClaim("username"))
                .claim("roles", claims.getStringListClaim("roles"))
                .claim("type", claims.getStringClaim("type"))
                .jti(claims.getJWTID())
                .build();
        } catch (Exception e) {
            return null;
        }
    }
}
