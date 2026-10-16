package dev.zhiji.control.model;

/**
 * User roles for RBAC.
 *
 * <p>USER: standard authenticated user, can submit and manage own tasks.
 * ADMIN: full access, can manage users and view all tasks.
 */
public enum Role {
    USER,
    ADMIN
}
