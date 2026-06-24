class Animal { };
class Dog inherits Animal { };
class Cat inherits Animal { };

class Main {
    main() : Object {
        let a : Animal <- new Dog in
            case a of
                d : Dog => 1;
                c : Cat => 2;
                o : Object => 3;
            esac
    };
};
