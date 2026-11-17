# Автономная сборка фильтра hpcups (HPLIP 3.25.8) без D-Bus и без закрытой ImageProcessor.
# Linux:  make            macOS arm64:  make JPEG=<prefix libjpeg>
JPEG    ?= /usr
CXX     ?= c++
CC      ?= cc
DEFS     = -DCONFDIR=\"/Library/Printers/HPSmartTank520\" -DAPDK_LITTLE_ENDIAN -DDISABLE_IMAGEPROCESSOR -DNDEBUG
CFLAGS  += -O2 -w $(DEFS) -I. -I$(JPEG)/include
CXXFLAGS+= -O2 -w $(DEFS) -I. -I$(JPEG)/include -std=gnu++11
LDLIBS   = $(JPEG_LIB) -lcups -lz
JPEG_LIB ?= -L$(JPEG)/lib -ljpeg
SRCS_CPP = $(wildcard *.cpp)
SRCS_C   = $(wildcard *.c)
OBJS     = $(SRCS_CPP:.cpp=.o) $(SRCS_C:.c=.o)
hpcups: $(OBJS)
	$(CXX) $(LDFLAGS) -o $@ $(OBJS) $(LDLIBS)
clean:
	rm -f *.o hpcups
