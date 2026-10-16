package dev.zhiji.control.service;

import dev.zhiji.control.model.Role;
import dev.zhiji.control.model.User;
import dev.zhiji.control.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.UUID;

/**
 * Registration and authentication logic.
 *
 * <p>Uses BCryptPasswordEncoder (Spring Security) instead of the Python
 * self-built bcrypt + HMAC approach. Passwords are never returned in
 * any API response.
 */
@Service
public class AuthService {

    private static final Logger log = LoggerFactory.getLogger(AuthService.class);

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;

    public AuthService(UserRepository userRepository,
                       PasswordEncoder passwordEncoder,
                       JwtService jwtService) {
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
        this.jwtService = jwtService;
    }

    @Transactional
    public User register(String username, String password, String displayName) {
        if (username == null || username.isBlank()) {
            throw new IllegalArgumentException("用户名不能为空");
        }
        if (username.length() < 3 || username.length() > 32) {
            throw new IllegalArgumentException("用户名长度需为 3-32 个字符");
        }
        if (!username.matches("^[a-z0-9_.-]+$")) {
            throw new IllegalArgumentException("用户名只能包含字母、数字、下划线、点或连字符");
        }
        if (password == null || password.length() < 8) {
            throw new IllegalArgumentException("密码至少需要 8 位");
        }
        if (userRepository.existsByUsername(username)) {
            throw new IllegalStateException("用户名已被占用: " + username);
        }

        String hash = passwordEncoder.encode(password);
        String id = UUID.randomUUID().toString();
        User user = new User(id, username, hash, displayName);
        return userRepository.save(user);
    }

    /**
     * Authenticate by username + password. Returns the user if valid.
     *
     * @throws IllegalArgumentException if credentials are invalid
     */
    public User authenticate(String username, String password) {
        User user = userRepository.findByUsername(username)
            .orElseThrow(() -> new IllegalArgumentException("用户名或密码不正确"));
        if (!user.isActive()) {
            throw new IllegalArgumentException("用户名或密码不正确");
        }
        if (!passwordEncoder.matches(password, user.getPasswordHash())) {
            throw new IllegalArgumentException("用户名或密码不正确");
        }
        return user;
    }

    /**
     * Issue tokens for an authenticated user.
     */
    public dev.zhiji.control.dto.TokenResponse login(String username, String password) {
        User user = authenticate(username, password);
        List<Role> roles = List.copyOf(user.getRoles());
        return jwtService.issueTokens(user.getId(), user.getUsername(), roles);
    }

    /**
     * Refresh tokens using a valid refresh token.
     */
    public dev.zhiji.control.dto.TokenResponse refresh(String refreshToken, String userId) {
        User user = userRepository.findById(userId)
            .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
        if (!user.isActive()) {
            throw new IllegalArgumentException("用户已被禁用");
        }
        List<Role> roles = List.copyOf(user.getRoles());
        return jwtService.refresh(refreshToken, userId, user.getUsername(), roles);
    }

    public User getUser(String userId) {
        return userRepository.findById(userId)
            .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
    }
}
