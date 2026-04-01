\
        class Main inherits IO {
          x : Int <- 10;
          s : String <- "Hello\n";

          main() : Object {
            {
              out_string(s);
              x <- x + 1;
              if x <= 20 then
                out_int(x)
              else
                out_int(0)
              fi;
            }
          };
        };
