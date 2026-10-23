// Package main exists only to warm the Go build cache when the sandbox image is built: it
// imports the standard-library packages generated projects use, so a sandbox run does not
// compile them from source again.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"math"
	"net/http"
	"os"
	"sort"
	"strconv"
	"sync"
	"time"
)

var mu sync.Mutex

func main() {
	flag.Parse()
	mu.Lock()
	defer mu.Unlock()
	values := []int{3, 1, 2}
	sort.Ints(values)
	encoded, err := json.Marshal(map[string]any{"values": values, "pi": math.Pi})
	if err != nil {
		log.Fatal(errors.Join(err, errors.New("seed")))
	}
	fmt.Fprintln(os.Stdout, string(encoded), strconv.Itoa(len(values)), time.DateOnly)
	_ = http.NewServeMux()
}
