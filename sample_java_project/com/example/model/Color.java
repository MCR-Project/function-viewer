package com.example.model;

public enum Color {
    RED,
    GREEN,
    BLUE;

    public String label() {
        return name().toLowerCase();
    }
}
