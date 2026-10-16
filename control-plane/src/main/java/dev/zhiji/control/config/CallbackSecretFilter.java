package dev.zhiji.control.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * Authenticates the internal callback endpoint using a shared secret.
 *
 * <p>Worker sends X-Callback-Secret header when posting events to
 * /internal/v1/tasks/{taskId}/events. Only requests matching the
 * configured secret are allowed; others get 401.
 */
public class CallbackSecretFilter extends OncePerRequestFilter {

    private final CallbackProperties props;

    public CallbackSecretFilter(CallbackProperties props) {
        this.props = props;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        String path = request.getRequestURI();
        if (path != null && path.startsWith("/internal/") && !isHealthCheck(path)) {
            String secret = request.getHeader("X-Callback-Secret");
            if (secret == null || !secret.equals(props.sharedSecret())) {
                response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
                response.setContentType("application/json");
                response.getWriter().write("{\"error\":{\"code\":\"INVALID_SECRET\",\"message\":\"Callback secret missing or invalid\"}}");
                return;
            }
        }
        filterChain.doFilter(request, response);
    }

    private boolean isHealthCheck(String path) {
        return path.contains("/health");
    }
}
