# Host tests for the pure C modules (no ESP-IDF). Usage: make -C firmware/test
CC ?= cc
CFLAGS = -std=c17 -Wall -Wextra -Werror -fsanitize=address,undefined -I../main
BUILD ?= $(or $(TMPDIR),/tmp)/ota-firmware-test

test: $(BUILD)/test_validate $(BUILD)/test_proto
	$(BUILD)/test_validate
	$(BUILD)/test_proto

$(BUILD)/test_validate: test_validate.c ../main/validate.c ../main/validate.h
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -o $@ test_validate.c ../main/validate.c

$(BUILD)/test_proto: test_proto.c ../main/proto.c ../main/proto.h
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -o $@ test_proto.c ../main/proto.c -lm

.PHONY: test
