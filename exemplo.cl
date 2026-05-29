class B inherits A {
    value() : Int {
        2
    };
};

class A {
    value() : Int {
        1
    };

    same() : SELF_TYPE {
        self
    };
};

class Main {
    a : A <- new B;

    main() : Object {
        {
            a.value();
            a <- new B;
            0;
        }
    };
};
