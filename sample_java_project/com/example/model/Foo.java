package com.example.model;

import com.example.service.EnglishGreeter;

/** A small record-holder with a couple of overloaded entry points. */
public class Foo {
    private final int id;

    public Foo(int id) {
        this.id = id;
        this.describe();
    }

    private void describe() {
        System.out.println("Foo #" + id);
    }

    public static void process(int amount) {
        System.out.println("processing amount " + amount);
    }

    public static void process(String label) {
        System.out.println("processing " + label);
    }

    /** Greets the current user through a class in another package. */
    public String greetUser() {
        EnglishGreeter greeter = new EnglishGreeter();
        return greeter.shout();
    }
}
