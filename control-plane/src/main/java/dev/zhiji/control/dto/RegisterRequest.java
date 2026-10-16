package dev.zhiji.control.dto;

import jakarta.validation.constraints.NotBlank;

public record RegisterRequest(
    @NotBlank String username,
    @NotBlank String password,
    String displayName
) {}
