package dev.zhiji.control.dto;

import java.time.Instant;
import java.util.List;

/**
 * User info returned by GET /api/v1/auth/me.
 */
public record UserInfo(
    String id,
    String username,
    String displayName,
    List<String> roles,
    Instant createdAt
) {}
