package dev.zhiji.control.dto;

import java.util.Map;

/**
 * Error response matching the OpenAPI contract's ErrorResponse schema.
 */
public record ErrorResponse(Map<String, String> error) {

    public ErrorResponse(String code, String message) {
        this(Map.of(code, message));
    }
}
