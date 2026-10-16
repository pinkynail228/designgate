;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC

(ns frontend-tests.util-theme-test
  (:require
   [app.main.data.profile :as profile]
   [app.util.theme :as theme]
   [cljs.test :as t :include-macros true]))

(t/deftest resolve-theme-unset-falls-back-to-light
  (t/testing "a nil profile theme resolves to light"
    (t/is (= "light" (theme/resolve-theme nil "light")))
    (t/is (= "light" (theme/resolve-theme nil "dark")))))

(t/deftest resolve-theme-legacy-default-resolves-to-light
  (t/testing "the legacy 'default' value resolves to light"
    (t/is (= "light" (theme/resolve-theme "default" "light")))
    (t/is (= "light" (theme/resolve-theme "default" "dark")))))

(t/deftest resolve-theme-explicit-values-are-unchanged
  (t/testing "explicit light and dark values are taken as-is"
    (t/is (= "light" (theme/resolve-theme "light" "light")))
    (t/is (= "light" (theme/resolve-theme "light" "dark")))
    (t/is (= "dark" (theme/resolve-theme "dark" "light")))
    (t/is (= "dark" (theme/resolve-theme "dark" "dark")))))

(t/deftest resolve-theme-system-follows-system-theme
  (t/testing "'system' follows the given system theme"
    (t/is (= "light" (theme/resolve-theme "system" "light")))
    (t/is (= "dark" (theme/resolve-theme "system" "dark")))))

(t/deftest dark-theme?-unset-is-light
  (t/testing "an unset profile theme is never dark"
    (t/is (false? (theme/dark-theme? nil "light")))
    (t/is (false? (theme/dark-theme? nil "dark")))))

(t/deftest dark-theme?-legacy-default-is-light
  (t/testing "the legacy 'default' value is never dark"
    (t/is (false? (theme/dark-theme? "default" "light")))
    (t/is (false? (theme/dark-theme? "default" "dark")))))

(t/deftest dark-theme?-explicit-and-system-dark
  (t/testing "an explicit dark theme, or system on a dark OS, is dark"
    (t/is (true? (theme/dark-theme? "dark" "light")))
    (t/is (true? (theme/dark-theme? "system" "dark")))
    (t/is (false? (theme/dark-theme? "system" "light")))))

(t/deftest next-theme-follows-the-cycle
  (t/testing "the theme cycle goes dark -> light -> system -> dark"
    (t/is (= "light" (profile/next-theme "dark")))
    (t/is (= "system" (profile/next-theme "light")))
    (t/is (= "dark" (profile/next-theme "system")))))

(t/deftest next-theme-unset-counts-as-light
  (t/testing "an unset theme behaves as light and advances to system"
    (t/is (= "system" (profile/next-theme nil)))))

(t/deftest next-theme-legacy-default-counts-as-light
  (t/testing "the legacy 'default' value behaves as light"
    (t/is (= "system" (profile/next-theme "default")))))
