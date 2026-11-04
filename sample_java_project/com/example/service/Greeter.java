package com.example.service;

public interface Greeter {
    default String greet() {
        return "Hello from " + name();
    }

    String name();
}
