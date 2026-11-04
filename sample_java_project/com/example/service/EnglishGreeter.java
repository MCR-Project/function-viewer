package com.example.service;

public class EnglishGreeter implements Greeter {
    public EnglishGreeter() {
        System.out.println("ready to greet");
    }

    @Override
    public String name() {
        return "the English greeter";
    }

    public String shout() {
        Formatter formatter = new Formatter();
        return formatter.upper(this.name());
    }

    static class Formatter {
        String upper(String text) {
            return text.toUpperCase();
        }
    }
}
