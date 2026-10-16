package dev.zhiji.control.dto;

import java.util.List;

/**
 * Token response returned by login, register, and refresh endpoints.
 */
public record TokenResponse(
    String accessToken,
    String refreshToken,
    long expiresIn,
    String tokenType,
    String username,
    List<String> roles
) {}
