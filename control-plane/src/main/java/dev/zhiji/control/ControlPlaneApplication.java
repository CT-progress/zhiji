package dev.zhiji.control;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Zhiji control plane entry point.
 *
 * <p>Java takes over control-plane responsibilities (auth, orchestration, quota, audit);
 * Python remains the data-plane worker (platform adapters, transcription, LLM processing).
 * The contract between the two is defined in {@code docs/contracts/worker-api.yaml}.
 */
@SpringBootApplication
public class ControlPlaneApplication {

    public static void main(String[] args) {
        SpringApplication.run(ControlPlaneApplication.class, args);
    }
}
