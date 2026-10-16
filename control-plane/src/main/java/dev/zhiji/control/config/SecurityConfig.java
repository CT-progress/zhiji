package dev.zhiji.control.config;

import com.nimbusds.jose.jwk.source.ImmutableSecret;
import com.nimbusds.jose.proc.SecurityContext;
import dev.zhiji.control.service.TokenRevocationService;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter;
import org.springframework.security.oauth2.server.resource.authentication.JwtGrantedAuthoritiesConverter;
import org.springframework.security.oauth2.server.resource.web.authentication.BearerTokenAuthenticationFilter;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.www.BasicAuthenticationFilter;

import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;

/**
 * Spring Security configuration: JWT resource server + route-level authorization.
 *
 * <p>Routes:
 * <ul>
 *   <li>POST /api/v1/auth/register, /login, /refresh — public</li>
 *   <li>POST /internal/v1/tasks/{taskId}/events — callback, protected by shared secret filter</li>
 *   <li>Everything else under /api/** — requires authentication</li>
 *   <li>ADMIN role required for /api/v1/admin/**</li>
 * </ul>
 */
@Configuration
@EnableWebSecurity
@EnableConfigurationProperties({JwtProperties.class, CallbackProperties.class})
public class SecurityConfig {

    private final JwtProperties jwtProps;
    private final CallbackProperties callbackProps;
    private final TokenRevocationService revocationService;

    public SecurityConfig(JwtProperties jwtProps,
                          CallbackProperties callbackProps,
                          TokenRevocationService revocationService) {
        this.jwtProps = jwtProps;
        this.callbackProps = callbackProps;
        this.revocationService = revocationService;
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder(12);
    }

    @Bean
    public JwtDecoder jwtDecoder() {
        byte[] keyBytes = jwtProps.secret().getBytes(StandardCharsets.UTF_8);
        SecretKeySpec key = new SecretKeySpec(keyBytes, "HmacSHA256");
        return NimbusJwtDecoder.withSecretKey(key).build();
    }

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        // Convert JWT "roles" claim into Spring authorities with ROLE_ prefix
        JwtGrantedAuthoritiesConverter authoritiesConverter = new JwtGrantedAuthoritiesConverter();
        authoritiesConverter.setAuthoritiesClaimName("roles");
        authoritiesConverter.setAuthorityPrefix("ROLE_");

        JwtAuthenticationConverter jwtAuthConverter = new JwtAuthenticationConverter();
        jwtAuthConverter.setJwtGrantedAuthoritiesConverter(authoritiesConverter);

        http
            .csrf(csrf -> csrf.disable())
            .sessionManagement(sm -> sm.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .authorizeHttpRequests(auth -> auth
                // Public auth endpoints
                .requestMatchers(HttpMethod.POST, "/api/v1/auth/register").permitAll()
                .requestMatchers(HttpMethod.POST, "/api/v1/auth/login").permitAll()
                .requestMatchers(HttpMethod.POST, "/api/v1/auth/refresh").permitAll()
                // Internal callback endpoint — protected by shared secret filter
                .requestMatchers("/internal/**").permitAll()
                // Admin-only
                .requestMatchers("/api/v1/admin/**").hasRole("ADMIN")
                // Everything else under /api requires authentication
                .requestMatchers("/api/**").authenticated()
                .anyRequest().permitAll()
            )
            .oauth2ResourceServer(oauth2 -> oauth2
                .jwt(jwt -> jwt.jwtAuthenticationConverter(jwtAuthConverter))
            )
            // Check token revocation after JWT authentication
            .addFilterAfter(
                new TokenRevocationFilter(revocationService),
                BearerTokenAuthenticationFilter.class
            )
            // Shared-secret filter for callback endpoint
            .addFilterBefore(
                new CallbackSecretFilter(callbackProps),
                BearerTokenAuthenticationFilter.class
            );

        return http.build();
    }
}
