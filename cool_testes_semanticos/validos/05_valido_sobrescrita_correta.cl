class A {
    f(x : Int) : Int {
        x + 1
    };
};

class Main inherits A {
    f(x : Int) : Int {
        x + 2
    };

    main() : Object {
        f(10)
    };
};
