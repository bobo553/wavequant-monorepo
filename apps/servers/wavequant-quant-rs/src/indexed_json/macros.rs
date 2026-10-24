// JSON literal parser adapted from serde_json 1.0.154 (MIT OR Apache-2.0).
#[macro_export]
macro_rules! indexed_json_macro {
    // Hide distracting implementation details from the generated rustdoc.
    ($($json:tt)+) => {
        $crate::indexed_json_internal!($($json)+)
    };
}

// Rocket relies on this because they export their own `json!` with a different
// doc comment than ours, and various Rust bugs prevent them from calling our
// `json!` from their `json!` so they call `indexed_json_internal!` directly. Check with
// @SergioBenitez before making breaking changes to this macro.
//
// Changes are fine as long as `indexed_json_internal!` does not call any new helper
// macros and can still be invoked as `indexed_json_internal!($($json)+)`.
#[macro_export]
#[doc(hidden)]
macro_rules! indexed_json_internal {
    //////////////////////////////////////////////////////////////////////////
    // TT muncher for parsing the inside of an array [...]. Produces a vec![...]
    // of the elements.
    //
    // Must be invoked as: indexed_json_internal!(@array [] $($tt)*)
    //////////////////////////////////////////////////////////////////////////

    // Done with trailing comma.
    (@array [$($elems:expr,)*]) => {
        ::std::vec![$($elems,)*]
    };

    // Done without trailing comma.
    (@array [$($elems:expr),*]) => {
        ::std::vec![$($elems),*]
    };

    // Next element is `null`.
    (@array [$($elems:expr,)*] null $($rest:tt)*) => {
        $crate::indexed_json_internal!(@array [$($elems,)* $crate::indexed_json_internal!(null)] $($rest)*)
    };

    // Next element is `true`.
    (@array [$($elems:expr,)*] true $($rest:tt)*) => {
        $crate::indexed_json_internal!(@array [$($elems,)* $crate::indexed_json_internal!(true)] $($rest)*)
    };

    // Next element is `false`.
    (@array [$($elems:expr,)*] false $($rest:tt)*) => {
        $crate::indexed_json_internal!(@array [$($elems,)* $crate::indexed_json_internal!(false)] $($rest)*)
    };

    // Next element is an array.
    (@array [$($elems:expr,)*] [$($array:tt)*] $($rest:tt)*) => {
        $crate::indexed_json_internal!(@array [$($elems,)* $crate::indexed_json_internal!([$($array)*])] $($rest)*)
    };

    // Next element is a map.
    (@array [$($elems:expr,)*] {$($map:tt)*} $($rest:tt)*) => {
        $crate::indexed_json_internal!(@array [$($elems,)* $crate::indexed_json_internal!({$($map)*})] $($rest)*)
    };

    // Next element is an expression followed by comma.
    (@array [$($elems:expr,)*] $next:expr, $($rest:tt)*) => {
        $crate::indexed_json_internal!(@array [$($elems,)* $crate::indexed_json_internal!($next),] $($rest)*)
    };

    // Last element is an expression with no trailing comma.
    (@array [$($elems:expr,)*] $last:expr) => {
        $crate::indexed_json_internal!(@array [$($elems,)* $crate::indexed_json_internal!($last)])
    };

    // Comma after the most recent element.
    (@array [$($elems:expr),*] , $($rest:tt)*) => {
        $crate::indexed_json_internal!(@array [$($elems,)*] $($rest)*)
    };

    // Unexpected token after most recent element.
    (@array [$($elems:expr),*] $unexpected:tt $($rest:tt)*) => {
        $crate::indexed_json_unexpected!($unexpected)
    };

    //////////////////////////////////////////////////////////////////////////
    // TT muncher for parsing the inside of an object {...}. Each entry is
    // inserted into the given map variable.
    //
    // Must be invoked as: indexed_json_internal!(@object $map () ($($tt)*) ($($tt)*))
    //
    // We require two copies of the input tokens so that we can match on one
    // copy and trigger errors on the other copy.
    //////////////////////////////////////////////////////////////////////////

    // Done.
    (@object $object:ident () () ()) => {};

    // Insert the current entry followed by trailing comma.
    (@object $object:ident [$($key:tt)+] ($value:expr) , $($rest:tt)*) => {
        let _ = $object.insert(($($key)+).into(), $value);
        $crate::indexed_json_internal!(@object $object () ($($rest)*) ($($rest)*));
    };

    // Current entry followed by unexpected token.
    (@object $object:ident [$($key:tt)+] ($value:expr) $unexpected:tt $($rest:tt)*) => {
        $crate::indexed_json_unexpected!($unexpected);
    };

    // Insert the last entry without trailing comma.
    (@object $object:ident [$($key:tt)+] ($value:expr)) => {
        let _ = $object.insert(($($key)+).into(), $value);
    };

    // Next value is `null`.
    (@object $object:ident ($($key:tt)+) (: null $($rest:tt)*) $copy:tt) => {
        $crate::indexed_json_internal!(@object $object [$($key)+] ($crate::indexed_json_internal!(null)) $($rest)*);
    };

    // Next value is `true`.
    (@object $object:ident ($($key:tt)+) (: true $($rest:tt)*) $copy:tt) => {
        $crate::indexed_json_internal!(@object $object [$($key)+] ($crate::indexed_json_internal!(true)) $($rest)*);
    };

    // Next value is `false`.
    (@object $object:ident ($($key:tt)+) (: false $($rest:tt)*) $copy:tt) => {
        $crate::indexed_json_internal!(@object $object [$($key)+] ($crate::indexed_json_internal!(false)) $($rest)*);
    };

    // Next value is an array.
    (@object $object:ident ($($key:tt)+) (: [$($array:tt)*] $($rest:tt)*) $copy:tt) => {
        $crate::indexed_json_internal!(@object $object [$($key)+] ($crate::indexed_json_internal!([$($array)*])) $($rest)*);
    };

    // Next value is a map.
    (@object $object:ident ($($key:tt)+) (: {$($map:tt)*} $($rest:tt)*) $copy:tt) => {
        $crate::indexed_json_internal!(@object $object [$($key)+] ($crate::indexed_json_internal!({$($map)*})) $($rest)*);
    };

    // Next value is an expression followed by comma.
    (@object $object:ident ($($key:tt)+) (: $value:expr , $($rest:tt)*) $copy:tt) => {
        $crate::indexed_json_internal!(@object $object [$($key)+] ($crate::indexed_json_internal!($value)) , $($rest)*);
    };

    // Last value is an expression with no trailing comma.
    (@object $object:ident ($($key:tt)+) (: $value:expr) $copy:tt) => {
        $crate::indexed_json_internal!(@object $object [$($key)+] ($crate::indexed_json_internal!($value)));
    };

    // Missing value for last entry. Trigger a reasonable error message.
    (@object $object:ident ($($key:tt)+) (:) $copy:tt) => {
        // "unexpected end of macro invocation"
        $crate::indexed_json_internal!();
    };

    // Missing colon and value for last entry. Trigger a reasonable error
    // message.
    (@object $object:ident ($($key:tt)+) () $copy:tt) => {
        // "unexpected end of macro invocation"
        $crate::indexed_json_internal!();
    };

    // Misplaced colon. Trigger a reasonable error message.
    (@object $object:ident () (: $($rest:tt)*) ($colon:tt $($copy:tt)*)) => {
        // Takes no arguments so "no rules expected the token `:`".
        $crate::indexed_json_unexpected!($colon);
    };

    // Found a comma inside a key. Trigger a reasonable error message.
    (@object $object:ident ($($key:tt)*) (, $($rest:tt)*) ($comma:tt $($copy:tt)*)) => {
        // Takes no arguments so "no rules expected the token `,`".
        $crate::indexed_json_unexpected!($comma);
    };

    // Key is fully parenthesized. This avoids clippy double_parens false
    // positives because the parenthesization may be necessary here.
    (@object $object:ident () (($key:expr) : $($rest:tt)*) $copy:tt) => {
        $crate::indexed_json_internal!(@object $object ($key) (: $($rest)*) (: $($rest)*));
    };

    // Refuse to absorb colon token into key expression.
    (@object $object:ident ($($key:tt)*) (: $($unexpected:tt)+) $copy:tt) => {
        $crate::indexed_json_expect_expr_comma!($($unexpected)+);
    };

    // Munch a token into the current key.
    (@object $object:ident ($($key:tt)*) ($tt:tt $($rest:tt)*) $copy:tt) => {
        $crate::indexed_json_internal!(@object $object ($($key)* $tt) ($($rest)*) ($($rest)*));
    };

    //////////////////////////////////////////////////////////////////////////
    // The main implementation.
    //
    // Must be invoked as: indexed_json_internal!($($json)+)
    //////////////////////////////////////////////////////////////////////////

    (null) => {
        $crate::indexed_json::Value::Null
    };

    (true) => {
        $crate::indexed_json::Value::Bool(true)
    };

    (false) => {
        $crate::indexed_json::Value::Bool(false)
    };

    ([]) => {
        $crate::indexed_json::Value::Array(::std::vec![])
    };

    ([ $($tt:tt)+ ]) => {
        $crate::indexed_json::Value::Array($crate::indexed_json_internal!(@array [] $($tt)+))
    };

    ({}) => {
        $crate::indexed_json::Value::Object($crate::indexed_json::Map::new())
    };

    ({ $($tt:tt)+ }) => {
        $crate::indexed_json::Value::Object({
            let mut object = $crate::indexed_json::Map::new();
            $crate::indexed_json_internal!(@object object () ($($tt)+) ($($tt)+));
            object
        })
    };

    // Any Serialize type: numbers, strings, struct literals, variables etc.
    // Must be below every other rule.
    ($other:expr) => {
        $crate::indexed_json::to_value(&$other).unwrap()
    };
}

// Used by old versions of Rocket.
// Unused since https://github.com/rwf2/Rocket/commit/c74bcfd40a47b35330db6cafb88e4f3da83e0d17
#[macro_export]
#[doc(hidden)]
macro_rules! indexed_json_internal_vec {
    ($($content:tt)*) => {
        vec![$($content)*]
    };
}

#[macro_export]
#[doc(hidden)]
macro_rules! indexed_json_unexpected {
    () => {};
}

#[macro_export]
#[doc(hidden)]
macro_rules! indexed_json_expect_expr_comma {
    ($e:expr , $($tt:tt)*) => {};
}
