class Builder {
    init() : SELF_TYPE {
        self
    };
};

class Main {
    b : Builder <- (new Builder).init();

    main() : Object {
        b
    };
};
