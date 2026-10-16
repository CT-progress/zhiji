package dev.zhiji.control.model;

import java.util.List;

/**
 * Worker health response.
 */
public record WorkerHealth(
    String status,
    String version,
    List<String> capabilities
) {}
