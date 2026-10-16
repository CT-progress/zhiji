package dev.zhiji.control.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Shared secret for internal callback authentication.
 *
 * <p>The worker includes this in the X-Callback-Secret header when posting
 * event callbacks. This is separate from JWT — the callback endpoint is
 * internal (machine-to-machine), not user-facing.
 */
@ConfigurationProperties(prefix = "zhiji.callback")
public record CallbackProperties(String sharedSecret) {}
