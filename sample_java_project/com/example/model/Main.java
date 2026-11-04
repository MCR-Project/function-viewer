package com.example.model;

import java.util.List;

/** Wires the other classes in this package together, same-package, no imports needed for them. */
public class Main {
    public void run() {
        Foo foo = new Foo(1);
        Color color = Color.RED;
        System.out.println(color.label());

        Point point = new Point(2, 3);
        System.out.println(point.sum());

        List<Foo> foos = List.of(new Foo(2), new Foo(3));
        foos.forEach(f -> foo.greetUser());

        Foo.process(42);
    }
}
