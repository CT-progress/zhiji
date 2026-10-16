package dev.zhiji.control.config;

import dev.zhiji.control.service.TokenRevocationService;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * Checks whether the authenticated JWT's jti has been revoked.
 *
 * <p>Runs after BearerTokenAuthenticationFilter so the JWT is already decoded.
 */
public class TokenRevocationFilter extends OncePerRequestFilter {

    private final TokenRevocationService revocationService;

    public TokenRevocationFilter(TokenRevocationService revocationService) {
        this.revocationService = revocationService;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth != null && auth.getPrincipal() instanceof Jwt jwt) {
            String jti = jwt.getId();
            if (jti != null && revocationService.isRevoked(jti)) {
                response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
                response.setContentType("application/json");
                response.getWriter().write("{\"error\":{\"code\":\"TOKEN_REVOKED\",\"message\":\"Token has been revoked\"}}");
                return;
            }
        }
        filterChain.doFilter(request, response);
    }
}
