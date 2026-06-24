class Main inherits IO {
    x : Int <- 10;
    msg : String <- "Resultado: ";

    main() : Object {
        {
            out_string(msg);
            out_int(x);
            if x < 20 then out_string("\nmenor que 20\n") else out_string("\nmaior ou igual a 20\n") fi;
            0;
        }
    };
};
