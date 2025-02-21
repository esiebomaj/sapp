#!/usr/bin/env python3
# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from typing import Generator
from unittest import TestCase, mock

from ..decorators import (
    UserError,
    catch_keyboard_interrupt,
    catch_user_error,
    log_time,
    retryable,
)


class RetryableTest(TestCase):
    def setUp(self) -> None:
        self.times_through = 0

    @retryable(num_tries=5)
    def semiRaiseException(self) -> bool:
        self.times_through += 1
        if self.times_through < 3:
            raise Exception("You are killing me.")
        else:
            return True

    @retryable(num_tries=5, retryable_exs=[ImportError])
    # pyre-fixme[3]: Return type must be annotated.
    def raiseRetryableException(self):
        self.times_through += 1
        if self.times_through < 3:
            raise ImportError
        else:
            raise Exception("You are killing me.")

    def testRetries(self) -> None:
        self.assertTrue(self.semiRaiseException())
        self.assertTrue(
            self.times_through == 3, "times_through = %d" % (self.times_through)
        )
        self.assertTrue(self.semiRaiseException())
        self.assertTrue(
            self.times_through == 4, "times_through = %d" % (self.times_through)
        )

    def testRetryableExceptions(self) -> None:
        self.assertRaises(Exception, lambda: self.raiseRetryableException())
        self.assertEqual(3, self.times_through)


def mocked_time_generator() -> Generator[float, None, None]:
    """
    Returns time in 10s increments
    """
    start = 578854800.0
    while True:
        yield start
        start += 10


@mock.patch("time.time", side_effect=mocked_time_generator())
class LogTimeTest(TestCase):
    @log_time
    def takes_some_time(self) -> None:
        pass

    # pyre-fixme[2]: Parameter must be annotated.
    def testBasic(self, mocked_time_generator) -> None:
        with self.assertLogs("sapp") as context_manager:
            self.takes_some_time()
        self.assertEqual(
            context_manager.output,
            [
                "INFO:sapp:Takes_Some_Time starting...",
                "INFO:sapp:Takes_Some_Time finished (0:00:20)",
            ],
        )


class CatchUserErrorTest(TestCase):
    @catch_user_error()
    # pyre-fixme[3]: Return type must be annotated.
    def throwsUserError(self):
        raise UserError

    def testCatchesUserError(self) -> None:
        try:
            self.throwsUserError()
        except UserError:
            self.fail("Unexpected UserError")

    @catch_user_error()
    # pyre-fixme[3]: Return type must be annotated.
    def throwsException(self):
        raise ValueError

    def testDoesNotCatchOtherExceptions(self) -> None:
        with self.assertRaises(ValueError):
            self.throwsException()


class CatchKeyboardInterruptTest(TestCase):
    @catch_keyboard_interrupt()
    # pyre-fixme[3]: Return type must be annotated.
    def throwsKeyboardInterrupt(self):
        raise KeyboardInterrupt

    def testCatchesKeyboardInterrupt(self) -> None:
        try:
            self.throwsKeyboardInterrupt()
        except KeyboardInterrupt:
            self.fail("Unexpected KeyboardInterrupt")

    @catch_keyboard_interrupt()
    # pyre-fixme[3]: Return type must be annotated.
    def throwsException(self):
        raise ValueError

    def testDoesNotCatchOtherExceptions(self) -> None:
        with self.assertRaises(ValueError):
            self.throwsException()
